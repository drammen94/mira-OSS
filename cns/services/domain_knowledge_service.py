"""
Domain Knowledge Service - Manages Letta-style memory blocks for domain-specific context.

MIRA's domain knowledge system uses Letta's sleeptime agents as a backend service to maintain
user-specific knowledge blocks (e.g., "work", "michigan_trip_planning"). These blocks are
automatically updated based on continuum context and can be selectively enabled/disabled
to inject relevant domain knowledge into MIRA's system prompt.

Integration Points:
- working_memory/trinkets/domain_knowledge_trinket.py: Fetches enabled blocks and injects
  them into the system prompt as XML-formatted sections
- cns/services/orchestrator.py: Calls buffer_message() after each continuum turn to
  feed messages to Letta sleeptime agents for block updates
- config/config.py: DomainKnowledgeConfig controls batching and caching behavior

Architecture:
MIRA owns the UX and orchestration - users manage blocks through MIRA's interface, and
MIRA controls when blocks are enabled/disabled. Letta handles the actual block content
updates via sleeptime agents that process message batches asynchronously.
"""
import json
import logging
import re
from xml.sax.saxutils import escape as xml_escape
from typing import List, Dict, Any, Optional

from letta_client import Letta

from clients.vault_client import get_api_key
from clients.valkey_client import get_valkey_client
from config.config import DomainKnowledgeConfig
from utils.database_session_manager import get_shared_session_manager
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class DomainKnowledgeService:
    """
    Service for managing domain-specific knowledge blocks via Letta API.

    Data Flow Overview:
    1. User enables a domain block via MIRA's UI (only one domain can be enabled at a time)
    2. Orchestrator calls buffer_message() after each continuum turn
    3. Messages accumulate in memory buffer for the enabled domain
    4. When buffer reaches batch_size, messages flush to Letta sleeptime agent (async)
    5. Letta agent receives domain-specific extraction instruction with message context
    6. Letta agent updates block content based on message context (focused on that domain only)
    7. Domain knowledge trinket fetches block content (via cache layers) for prompt injection
    8. Updated block appears in MIRA's system prompt on next request

    Each user can have multiple domain blocks (e.g., "work", "michigan_trip_planning")
    that are selectively enabled/disabled. However, ONLY ONE domain can be enabled at a time
    to ensure Letta sleeptime agents receive clear, focused extraction context without
    cross-contamination between domains. Blocks are managed by Letta sleeptime agents
    which automatically update them based on continuum context.

    Letta is used as a backend service - MIRA owns the UX and orchestration.

    **Tiered Caching:**
    Block content uses three-tier caching to minimize Letta API calls:
    1. Valkey (in-memory, 5-minute TTL, auto-expires)
    2. Database (persistent, slower than Valkey, see scripts/create_domain_knowledge_schema.sql)
    3. Letta API (slowest, external service)

    Each layer caches raw block content. Formatting happens on-demand to support both
    raw and XML-formatted requests from the same cache entry.

    **Buffer Persistence Risk:**
    Message buffers are stored in memory and lost on service restart. Up to message_batch_size-1
    messages per domain may be lost if service crashes before flush. This is acceptable
    given the low probability and minimal impact on block accuracy.

    **Concurrency Model:**
    Assumes single-threaded request processing per user - requests never overlap for the same user.
    """

    def __init__(self, event_bus):
        """
        Initialize domain knowledge service with optional Letta client.

        This is a singleton service (see get_domain_knowledge_service() factory function).
        The singleton pattern ensures consistent message buffering state across all callers
        and prevents multiple Letta client instances from being created.

        If Letta API key is not available, service gracefully disables itself.

        Args:
            event_bus: Event bus for subscribing to TurnCompletedEvent (required)
        """
        from config.config_manager import config
        self.config = config.domain_knowledge

        # Get Letta API key from Vault - optional, service disables if missing
        api_key = get_api_key("letta_key")
        if not api_key:
            logger.warning("Letta API key 'letta_key' not found in Vault - domain knowledge service disabled")
            self.enabled = False
            self.letta_client = None
            self.session_manager = None
            self.valkey = None
            self._message_buffers = {}
            return

        self.enabled = True
        self.letta_client = Letta(token=api_key)
        self.session_manager = get_shared_session_manager()

        # Initialize Valkey client for caching
        self.valkey = get_valkey_client()

        # Message buffer for batching - structure: {user_id: {domain_label: [messages]}}
        # TurnCompletedEvent handler calls buffer_message() after each turn, and when buffer
        # reaches config.message_batch_size (default: 10), messages are automatically flushed
        # to Letta asynchronously
        self._message_buffers = {}

        # Track consecutive flush failures to detect persistent Letta unavailability
        self._flush_failure_counts = {}  # Structure: {user_id: {domain_label: failure_count}}

        # Subscribe to turn completed events
        event_bus.subscribe('TurnCompletedEvent', self._handle_turn_completed)
        logger.info("DomainKnowledgeService subscribed to TurnCompletedEvent")

        logger.info("DomainKnowledgeService initialized with Letta API and Valkey cache")

    def _handle_turn_completed(self, event):
        """
        Handle TurnCompletedEvent by buffering continuum messages to Letta.

        Called automatically when a continuum turn completes. Buffers both
        user and assistant messages from the turn to all enabled domain blocks.

        Args:
            event: TurnCompletedEvent containing continuum object and metadata

        Raises:
            Exception: If database query fails or buffering infrastructure fails.
                      Event bus error handler will log with full stack trace.
        """
        if not self.enabled:
            return

        logger.debug(f"Handling turn completion for continuum {event.continuum_id}")

        # Get continuum from event (already in memory, no fetch needed)
        continuum = event.continuum

        # Get last two messages from this turn (user + assistant)
        if len(continuum.messages) < 2:
            logger.debug("Not enough messages in continuum for buffering")
            return

        messages = continuum.messages[-2:]

        # Buffer each message to enabled domains
        for msg in messages:
            # Extract text content for multimodal messages
            content = msg.content
            if isinstance(content, list):
                # Extract text from multimodal content array
                text_parts = [item['text'] for item in content if item.get('type') == 'text']
                content = ' '.join(text_parts) if text_parts else str(content)

            self.buffer_message(event.user_id, msg.role, content)

        logger.debug(f"Buffered {len(messages)} messages to Letta for user {event.user_id}")

    def _validate_domain_label(self, domain_label: str) -> None:
        """
        Validate domain_label format.

        Args:
            domain_label: Domain label to validate

        Raises:
            ValueError: If domain_label is invalid (must be snake_case: lowercase, digits, underscores only)
        """
        if not re.match(r'^[a-z0-9_]+$', domain_label):
            raise ValueError(
                f"Invalid domain_label '{domain_label}': must be snake_case "
                "(lowercase letters, digits, and underscores only)"
            )

    def _normalize_domain_label(self, domain_label: str) -> str:
        """
        Normalize domain_label to valid snake_case format.

        Args:
            domain_label: Raw domain label (e.g., "Michigan Trip", "Work-Notes")

        Returns:
            Normalized snake_case label (e.g., "michigan_trip", "work_notes")

        Raises:
            ValueError: If normalization results in empty string
        """
        # Lowercase and replace spaces/hyphens with underscores
        normalized = domain_label.lower().replace(' ', '_').replace('-', '_')

        # Remove invalid characters (keep only a-z, 0-9, _)
        normalized = re.sub(r'[^a-z0-9_]', '', normalized)

        # Collapse multiple underscores
        normalized = re.sub(r'_+', '_', normalized)

        # Strip leading/trailing underscores
        normalized = normalized.strip('_')

        if not normalized:
            raise ValueError(f"Domain label '{domain_label}' normalizes to empty string")

        return normalized

    def _get_cache_key(self, user_id: str, domain_label: str) -> str:
        """Generate Valkey cache key for domain block content."""
        return f"domain_block:{user_id}:{domain_label}"

    def _get_user_agent_id(self, user_id: str, domain_label: str) -> Optional[str]:
        """
        Get the Letta agent ID for a user's domain block.

        Args:
            user_id: User ID
            domain_label: Domain label (e.g., "work", "michigan_trip")

        Returns:
            Agent ID if exists, None otherwise
        """
        tag = f"{user_id}:{domain_label}"
        agents = self.letta_client.agents.list(tags=[tag], match_all_tags=True)
        return agents[0].id if agents else None

    def _create_sleeptime_agent(self, user_id: str, domain_label: str, block_description: str) -> str:
        """
        Create a sleeptime agent for managing a domain knowledge block.

        Sleeptime agents are Letta's pattern for background processing - they receive
        message batches asynchronously and update their memory blocks based on continuum
        context. MIRA creates one agent per domain per user.

        Agent lifecycle:
        1. Created via Letta API with unique tag ({user_id}:{domain_label})
        2. Given a memory block with 10k char limit and descriptive label
        3. Receives message batches via _flush_buffer() when buffer fills
        4. Asynchronously processes messages and updates block content
        5. Block content fetched by domain_knowledge_trinket.py for prompt injection

        Args:
            user_id: User ID
            domain_label: Domain label
            block_description: Description of what knowledge this block contains

        Returns:
            Created agent ID
        """
        tag = f"{user_id}:{domain_label}"
        agent_state = self.letta_client.agents.create(
            name=f"domain_{domain_label}_{user_id}",
            model=self.config.sleeptime_agent_model,
            agent_type="sleeptime_agent",
            initial_message_sequence=[],
            tags=[tag]
        )

        # Create the domain knowledge block
        block = self.letta_client.blocks.create(
            label=domain_label,
            description=block_description,
            limit=10000,  # 10k char limit
            value=""  # Start empty
        )

        # Attach block to agent
        self.letta_client.agents.blocks.attach(agent_id=agent_state.id, block_id=block.id)

        logger.info(f"Created sleeptime agent {agent_state.id} for domain '{domain_label}'")
        return agent_state.id

    def create_domain_block(
        self,
        domain_label: str,
        domain_name: str,
        block_description: str
    ) -> Dict[str, Any]:
        """
        Create a new domain knowledge block for a user.

        Dual-Write Pattern:
        MIRA maintains its own database (see scripts/create_domain_knowledge_schema.sql) that
        tracks which domains are enabled, when they were created, and caches block content.
        Letta stores the actual block content and handles updates via sleeptime agents.

        If Letta agent creation succeeds but database insert fails, the orphaned Letta agent
        is automatically cleaned up to maintain consistency.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label - will be normalized to snake_case (e.g., "Michigan Trip" → "michigan_trip")
            domain_name: Human-readable name (e.g., "Work", "Michigan Trip Planning")
            block_description: Description of what knowledge this block should contain

        Returns:
            Dict with agent_id and normalized domain_label

        Raises:
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()

        # Normalize domain_label to valid snake_case format
        domain_label = self._normalize_domain_label(domain_label)

        # Check if domain already exists
        existing_agent = self._get_user_agent_id(user_id, domain_label)
        if existing_agent:
            raise ValueError(f"Domain block '{domain_label}' already exists for user")

        # Create sleeptime agent via Letta API
        agent_id = self._create_sleeptime_agent(user_id, domain_label, block_description)

        # Store in MIRA's database with cleanup on failure
        try:
            with self.session_manager.get_session(user_id) as session:
                session.execute_update("""
                    INSERT INTO domain_knowledge_blocks
                    (user_id, domain_label, domain_name, block_description, agent_id, enabled, created_at)
                    VALUES (%(user_id)s, %(domain_label)s, %(domain_name)s, %(block_description)s, %(agent_id)s, FALSE, NOW())
                """, {
                    'user_id': user_id,
                    'domain_label': domain_label,
                    'domain_name': domain_name,
                    'block_description': block_description,
                    'agent_id': agent_id
                })

                # Initialize block content cache (blocks start empty)
                block_id_result = session.execute_query("""
                    SELECT id FROM domain_knowledge_blocks
                    WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
                """, {'user_id': user_id, 'domain_label': domain_label})

                if block_id_result:
                    session.execute_update("""
                        INSERT INTO domain_knowledge_block_content (block_id, block_value, synced_at)
                        VALUES (%(block_id)s, '', NOW())
                    """, {'block_id': block_id_result[0]['id']})
        except Exception as e:
            # Database insert failed - clean up orphaned Letta agent
            logger.error(f"Database insert failed for domain '{domain_label}', cleaning up Letta agent {agent_id}: {e}")
            try:
                self.letta_client.agents.delete(agent_id)
            except Exception as cleanup_error:
                logger.error(f"Failed to clean up Letta agent {agent_id}: {cleanup_error}")
            raise

        return {
            "agent_id": agent_id,
            "domain_label": domain_label,
            "domain_name": domain_name,
            "enabled": False
        }

    def get_block_content(self, domain_label: str, prompt_formatted: bool = True) -> Optional[str]:
        """
        Get the current content of a domain block using tiered caching.

        Caching strategy (optimized for read-heavy access patterns):
        1. Check Valkey (in-memory, ~1ms, 5-min TTL, auto-expires)
        2. Check database (persistent, ~10-50ms, see scripts/create_domain_knowledge_schema.sql)
        3. Fetch from Letta API (external service, ~100-500ms)

        Each tier caches raw block content. Formatting (XML wrapping) happens on-demand to
        support both raw and formatted requests from the same cache entry. Cache invalidation
        occurs automatically when blocks are updated via _flush_buffer() or update_block_content().

        Called by: working_memory/trinkets/domain_knowledge_trinket.py during system prompt
        composition to inject enabled blocks into MIRA's context.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label (must be valid snake_case)
            prompt_formatted: If True, return wrapped in XML tags for injection

        Returns:
            Block content (XML-wrapped if prompt_formatted), None if not found

        Raises:
            RuntimeError: If no user context is set
        """
        if not self.enabled:
            return None

        user_id = get_current_user_id()

        self._validate_domain_label(domain_label)
        cache_key = self._get_cache_key(user_id, domain_label)

        # Layer 1: Check Valkey cache (fastest) - stores raw value
        block_value = None
        block_description = None

        cached_data = self.valkey.get(cache_key)  # Raises if Valkey fails
        if cached_data:
            logger.debug(f"Valkey cache hit for {domain_label}")
            # Cache stores raw value + description as JSON
            cache_obj = json.loads(cached_data)
            block_value = cache_obj['value']
            block_description = cache_obj['description']

        # Layer 2: Check database (persistent cache)
        if block_value is None:
            with self.session_manager.get_session(user_id) as session:
                result = session.execute_query("""
                    SELECT dkb.id, dkb.block_description, dkbc.block_value
                    FROM domain_knowledge_blocks dkb
                    LEFT JOIN domain_knowledge_block_content dkbc ON dkb.id = dkbc.block_id
                    WHERE dkb.user_id = %(user_id)s AND dkb.domain_label = %(domain_label)s
                """, {'user_id': user_id, 'domain_label': domain_label})

                if not result:
                    return None

                block_id = result[0]['id']
                block_description = result[0]['block_description']
                block_value = result[0].get('block_value')

                # If found in database, cache to Valkey (raw value)
                if block_value:
                    logger.debug(f"Database cache hit for {domain_label}")

                    # Warm Valkey cache with database result
                    cache_obj = {'value': block_value, 'description': block_description}
                    self.valkey.setex(cache_key, self.config.block_cache_ttl, json.dumps(cache_obj))

        # Format if we have a value from either cache
        if block_value:
            if prompt_formatted:
                escaped_value = xml_escape(block_value)
                escaped_description = xml_escape(block_description, {'"': '&quot;'})
                return f'<{domain_label} description="{escaped_description}">{escaped_value}</{domain_label}>'
            return block_value

        # Layer 3: Fetch from Letta API (slowest)
        # If Letta API fails, let exception propagate - caller can distinguish infrastructure
        # failure from "block not found" (which was already handled above)
        logger.debug(f"Cache miss for {domain_label}, fetching from Letta")
        agent_id = self._get_user_agent_id(user_id, domain_label)
        if not agent_id:
            return None

        block = self.letta_client.agents.blocks.retrieve(agent_id, domain_label)

        # Update database cache (raises on infrastructure failure)
        with self.session_manager.get_session(user_id) as session:
            session.execute_update("""
                INSERT INTO domain_knowledge_block_content (block_id, block_value, letta_block_id, synced_at)
                VALUES (%(block_id)s, %(block_value)s, %(letta_block_id)s, NOW())
                ON CONFLICT (block_id) DO UPDATE SET
                    block_value = EXCLUDED.block_value,
                    letta_block_id = EXCLUDED.letta_block_id,
                    synced_at = NOW()
            """, {
                'block_id': block_id,
                'block_value': block.value,
                'letta_block_id': block.id if hasattr(block, 'id') else None
            })

        # Cache to Valkey (raw value)
        cache_obj = {'value': block.value, 'description': block.description}
        self.valkey.setex(cache_key, self.config.block_cache_ttl, json.dumps(cache_obj))

        # Format if requested
        if prompt_formatted:
            escaped_value = xml_escape(block.value)
            escaped_description = xml_escape(block.description, {'"': '&quot;'})
            return f'<{block.label} description="{escaped_description}">{escaped_value}</{block.label}>'

        return block.value

    def enable_domain(self, domain_label: str) -> bool:
        """
        Enable a domain block (inject into system prompt).

        IMPORTANT: Only one domain block can be enabled at a time. This constraint ensures
        that Letta sleeptime agents receive clear, focused context about what information
        to extract, preventing cross-contamination between domains.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label to enable (must be valid snake_case)

        Returns:
            True if enabled successfully

        Raises:
            ValueError: If another domain is already enabled or domain not found
            RuntimeError: If no user context is set
        """
        self._validate_domain_label(domain_label)

        user_id = get_current_user_id()
        with self.session_manager.get_session(user_id) as session:
            # Check if another domain is already enabled (single-domain constraint)
            currently_enabled = session.execute_query("""
                SELECT domain_label, domain_name
                FROM domain_knowledge_blocks
                WHERE user_id = %(user_id)s AND enabled = TRUE
            """, {'user_id': user_id})

            if currently_enabled:
                enabled_label = currently_enabled[0]['domain_label']
                enabled_name = currently_enabled[0]['domain_name']
                raise ValueError(
                    f"Cannot enable '{domain_label}': domain '{enabled_name}' ({enabled_label}) "
                    f"is already enabled. Only one domain block can be enabled at a time. "
                    f"Disable the current domain first."
                )

            # Enable the requested domain
            rows = session.execute_update("""
                UPDATE domain_knowledge_blocks
                SET enabled = TRUE, updated_at = NOW()
                WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
            """, {'user_id': user_id, 'domain_label': domain_label})

            if rows == 0:
                raise ValueError(f"Domain block '{domain_label}' not found")

            logger.info(f"Enabled domain '{domain_label}' for user {user_id}")
            return True

    def get_enabled_domains(self) -> List[Dict[str, Any]]:
        """
        Get all enabled domain blocks for a user.

        Requires: Active user context (set via set_current_user_id during authentication)

        Returns:
            List of enabled domain block info dicts

        Raises:
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()
        with self.session_manager.get_session(user_id) as session:
            return session.execute_query("""
                SELECT domain_label, domain_name, block_description, agent_id
                FROM domain_knowledge_blocks
                WHERE user_id = %(user_id)s AND enabled = TRUE
                ORDER BY created_at ASC
            """, {'user_id': user_id})

    def get_all_domains(self) -> List[Dict[str, Any]]:
        """
        Get all domain blocks for a user (enabled and disabled).

        Requires: Active user context (set via set_current_user_id during authentication)

        Returns:
            List of all domain block info dicts

        Raises:
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()
        with self.session_manager.get_session(user_id) as session:
            return session.execute_query("""
                SELECT domain_label, domain_name, block_description, agent_id, enabled
                FROM domain_knowledge_blocks
                WHERE user_id = %(user_id)s
                ORDER BY created_at ASC
            """, {'user_id': user_id})

    def buffer_message(self, user_id: str, role: str, content: str) -> None:
        """
        Add a message to the buffer for all enabled domains.

        Message Flow:
        1. cns/services/orchestrator.py calls this after each continuum turn
        2. Message added to in-memory buffer for each enabled domain
        3. When buffer reaches config.message_batch_size (default: 10), _flush_buffer()
           is automatically called
        4. Buffered messages sent to Letta sleeptime agent via async API
        5. Letta processes messages in background and updates block content
        6. Next time domain_knowledge_trinket.py fetches content, updated block is used

        Trade-off: In-memory buffering means up to (message_batch_size - 1) messages per
        domain may be lost on service restart. This is acceptable given low probability
        and minimal impact on block accuracy. Alternative would be database buffering with
        higher latency overhead.

        Note: This method still accepts user_id as parameter because it's called from
        event handler (_handle_turn_completed) which receives user_id from event object.
        Sets user context for downstream get_enabled_domains() call.

        Args:
            user_id: User ID from event
            role: Message role ("user" or "assistant")
            content: Message content
        """
        if not self.enabled:
            return

        # Set user context for get_enabled_domains() call
        from utils.user_context import set_current_user_id
        set_current_user_id(user_id)

        # Get enabled domains for user
        enabled_domains = self.get_enabled_domains()
        if not enabled_domains:
            return  # No enabled domains to update

        # Initialize user buffer if needed
        if user_id not in self._message_buffers:
            self._message_buffers[user_id] = {}

        # Add message to each enabled domain's buffer
        for domain in enabled_domains:
            domain_label = domain['domain_label']

            if domain_label not in self._message_buffers[user_id]:
                self._message_buffers[user_id][domain_label] = []

            self._message_buffers[user_id][domain_label].append({
                "role": role,
                "content": content
            })

            # Check if we should flush
            if len(self._message_buffers[user_id][domain_label]) >= self.config.message_batch_size:
                self._flush_buffer(user_id, domain_label)

    def _flush_buffer(self, user_id: str, domain_label: str) -> Optional[str]:
        """
        Flush buffered messages to Letta sleeptime agent.

        Async Processing Model:
        1. Batched messages sent to Letta via create_async() - returns immediately
        2. Letta sleeptime agent processes messages in background (seconds to minutes)
        3. Agent updates its memory block based on message content
        4. Cache invalidated immediately to prevent stale reads
        5. Next get_block_content() call fetches fresh content from Letta

        This async model prevents MIRA continuum flow from blocking on Letta processing.
        Brief staleness window is acceptable trade-off for responsiveness.

        Cache Invalidation Strategy:
        Both Valkey and database caches are invalidated on flush to force fresh fetch from
        Letta on next access. This ensures the domain_knowledge_trinket.py always gets the
        latest block content after Letta finishes processing.

        Args:
            user_id: User ID
            domain_label: Domain label

        Returns:
            Run ID if messages were sent, None if buffer was empty
        """
        # Get buffered messages
        if (user_id not in self._message_buffers or
            domain_label not in self._message_buffers[user_id] or
            not self._message_buffers[user_id][domain_label]):
            return None

        messages = self._message_buffers[user_id][domain_label]

        # Get agent ID and block description
        agent_id = self._get_user_agent_id(user_id, domain_label)
        if not agent_id:
            logger.warning(f"No agent found for domain '{domain_label}' - cannot flush messages")
            return None

        # Get block description for domain-specific extraction instruction
        with self.session_manager.get_session(user_id) as session:
            result = session.execute_query("""
                SELECT block_description
                FROM domain_knowledge_blocks
                WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
            """, {'user_id': user_id, 'domain_label': domain_label})

            if not result:
                logger.warning(f"No block description found for domain '{domain_label}'")
                return None

            block_description = result[0]['block_description']

        try:
            # Format message history
            message_history = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

            # Create domain-specific extraction instruction (per Letta engineer guidance)
            instruction = (
                f"EXTRACT INFORMATION ONLY FOR {domain_label}. "
                f"{domain_label} IS USED IN THIS CONTEXT: {block_description}. "
                f"THIS DOMAIN BLOCK IS NOT FOR GENERAL PURPOSE KNOWLEDGE."
            )

            # Combine instruction with message history
            formatted_messages = [{
                "role": "user",
                "content": f"{instruction}\n\nThe following message interactions have occurred:\n{message_history}"
            }]

            # Send formatted messages to Letta asynchronously
            letta_run = self.letta_client.agents.messages.create_async(
                agent_id=agent_id,
                messages=formatted_messages
            )

            # Invalidate cache - Letta will process async, content will be stale briefly
            cache_key = self._get_cache_key(user_id, domain_label)
            self.valkey.delete(cache_key)
            logger.debug(f"Invalidated Valkey cache for {domain_label} after flush")

            # Also invalidate database cache to force fresh fetch from Letta
            with self.session_manager.get_session(user_id) as session:
                session.execute_update("""
                    DELETE FROM domain_knowledge_block_content
                    WHERE block_id = (
                        SELECT id FROM domain_knowledge_blocks
                        WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
                    )
                """, {'user_id': user_id, 'domain_label': domain_label})

            # Clear buffer and clean up empty dictionaries
            del self._message_buffers[user_id][domain_label]

            # Remove empty user buffer dict if no more domains
            if not self._message_buffers[user_id]:
                del self._message_buffers[user_id]

            # Reset failure counter on successful flush
            if user_id in self._flush_failure_counts and domain_label in self._flush_failure_counts[user_id]:
                del self._flush_failure_counts[user_id][domain_label]
                if not self._flush_failure_counts[user_id]:
                    del self._flush_failure_counts[user_id]

            logger.info(f"Flushed {len(messages)} messages to domain '{domain_label}' (run_id={letta_run.id})")
            return letta_run.id

        except Exception as e:
            logger.error(f"Failed to flush messages to domain '{domain_label}': {e}")

            # Track consecutive failures to detect persistent Letta unavailability
            if user_id not in self._flush_failure_counts:
                self._flush_failure_counts[user_id] = {}
            if domain_label not in self._flush_failure_counts[user_id]:
                self._flush_failure_counts[user_id][domain_label] = 0

            self._flush_failure_counts[user_id][domain_label] += 1
            max_retries = 5  # Alert operators after 5 consecutive failures

            if self._flush_failure_counts[user_id][domain_label] >= max_retries:
                # Persistent failure - alert operators
                # Keep buffer intact - operators will fix Letta and buffer will flush on next attempt
                raise RuntimeError(
                    f"Letta service persistently unavailable for domain '{domain_label}'. "
                    f"Failed {max_retries} consecutive flush attempts. "
                    f"Buffer contains {len(messages)} messages awaiting flush. "
                    f"Domain knowledge updates will not occur until Letta service is restored."
                ) from e

            return None

    def flush_all_domains(self) -> Dict[str, str]:
        """
        Flush all buffered messages for a user's enabled domains.

        Requires: Active user context (set via set_current_user_id during authentication)

        Returns:
            Dict mapping domain_label to run_id

        Raises:
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()

        if user_id not in self._message_buffers:
            return {}

        run_ids = {}
        for domain_label in list(self._message_buffers[user_id].keys()):
            run_id = self._flush_buffer(user_id, domain_label)
            if run_id:
                run_ids[domain_label] = run_id

        return run_ids

    def disable_domain(self, domain_label: str) -> bool:
        """
        Disable a domain block (remove from system prompt).

        Flushes any buffered messages before disabling.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label to disable (must be valid snake_case)

        Returns:
            True if disabled successfully

        Raises:
            RuntimeError: If no user context is set
        """
        self._validate_domain_label(domain_label)

        user_id = get_current_user_id()

        # Flush buffered messages before disabling
        self._flush_buffer(user_id, domain_label)

        # Invalidate cache
        cache_key = self._get_cache_key(user_id, domain_label)
        self.valkey.delete(cache_key)
        logger.debug(f"Invalidated cache for {domain_label} on disable")

        with self.session_manager.get_session(user_id) as session:
            rows = session.execute_update("""
                UPDATE domain_knowledge_blocks
                SET enabled = FALSE, updated_at = NOW()
                WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
            """, {'user_id': user_id, 'domain_label': domain_label})

            if rows == 0:
                raise ValueError(f"Domain block '{domain_label}' not found")

            logger.info(f"Disabled domain '{domain_label}' for user {user_id}")
            return True

    def delete_domain(self, domain_label: str) -> bool:
        """
        Delete a domain block and its sleeptime agent.

        Flushes any buffered messages before deleting.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label to delete (must be valid snake_case)

        Returns:
            True if deleted successfully

        Raises:
            RuntimeError: If no user context is set
        """
        self._validate_domain_label(domain_label)

        user_id = get_current_user_id()

        # Flush buffered messages before deleting
        self._flush_buffer(user_id, domain_label)

        # Invalidate cache
        cache_key = self._get_cache_key(user_id, domain_label)
        self.valkey.delete(cache_key)
        logger.debug(f"Invalidated cache for {domain_label} on delete")

        agent_id = self._get_user_agent_id(user_id, domain_label)
        if agent_id:
            # Delete Letta agent (also deletes associated blocks)
            self.letta_client.agents.delete(agent_id)

        # Delete from MIRA's database
        with self.session_manager.get_session(user_id) as session:
            rows = session.execute_update("""
                DELETE FROM domain_knowledge_blocks
                WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
            """, {'user_id': user_id, 'domain_label': domain_label})

            if rows == 0:
                raise ValueError(f"Domain block '{domain_label}' not found")

        # Clean up buffer
        if (user_id in self._message_buffers and
            domain_label in self._message_buffers[user_id]):
            del self._message_buffers[user_id][domain_label]

        logger.info(f"Deleted domain '{domain_label}' for user {user_id}")
        return True

    def update_block_content(self, domain_label: str, new_content: str) -> bool:
        """
        Manually update a domain block's content.

        Requires: Active user context (set via set_current_user_id during authentication)

        Args:
            domain_label: Domain label to update (must be valid snake_case)
            new_content: New content for the block

        Returns:
            True if updated successfully

        Raises:
            RuntimeError: If no user context is set
        """
        self._validate_domain_label(domain_label)

        user_id = get_current_user_id()

        # Get agent ID
        agent_id = self._get_user_agent_id(user_id, domain_label)
        if not agent_id:
            raise ValueError(f"Domain block '{domain_label}' not found")

        try:
            # Get current block to preserve metadata
            block = self.letta_client.agents.blocks.retrieve(agent_id, domain_label)

            # Update block content via Letta API
            self.letta_client.blocks.modify(
                block_id=block.id,
                value=new_content
            )

            # Invalidate all caches to force refresh
            cache_key = self._get_cache_key(user_id, domain_label)
            self.valkey.delete(cache_key)
            logger.debug(f"Invalidated Valkey cache for {domain_label} after update")

            # Invalidate database cache
            with self.session_manager.get_session(user_id) as session:
                session.execute_update("""
                    DELETE FROM domain_knowledge_block_content
                    WHERE block_id = (
                        SELECT id FROM domain_knowledge_blocks
                        WHERE user_id = %(user_id)s AND domain_label = %(domain_label)s
                    )
                """, {'user_id': user_id, 'domain_label': domain_label})

            logger.info(f"Updated block content for domain '{domain_label}' for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update block content for domain '{domain_label}': {e}")
            raise ValueError(f"Failed to update block content: {str(e)}")


# Singleton instance
# Pattern ensures consistent message buffering state and single Letta client instance
# across all service consumers (orchestrator, trinket, API endpoints)
_service_instance = None


def get_domain_knowledge_service(event_bus) -> DomainKnowledgeService:
    """
    Get the singleton domain knowledge service instance.

    Singleton Pattern Rationale:
    - Message buffers (_message_buffers) must be shared across all callers to batch correctly
    - Multiple Letta client instances would create connection overhead and auth complexity
    - Service gracefully disables if Letta API credentials are unavailable

    Args:
        event_bus: Event bus for subscribing to TurnCompletedEvent (required, only used on first call)

    Returns:
        DomainKnowledgeService instance (enabled or disabled based on Letta availability)
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = DomainKnowledgeService(event_bus=event_bus)
    return _service_instance
