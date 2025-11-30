"""
Main continuum orchestrator for CNS.

Coordinates all continuum processing: message handling, LLM interaction,
tool execution, working memory updates, and event publishing.

Optimized to generate embeddings once and propagate them to all services.
"""
import logging
from typing import Dict, Any, List, Optional, Union

from cns.core.continuum import Continuum
from cns.core.events import (
    ContinuumEvent,
    TurnCompletedEvent
)
from clients.llm_provider import LLMProvider
from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider
from utils.tag_parser import TagParser

logger = logging.getLogger(__name__)


class ContinuumOrchestrator:
    """
    Main orchestration service for continuum processing.
    
    Coordinates the entire continuum flow from user input to final response,
    managing all system interactions through clean interfaces.
    """
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        continuum_repo,
        working_memory,
        tool_repo,
        tag_parser,
        fingerprint_generator,
        event_bus,
        memory_relevance_service
    ):
        """
        Initialize orchestrator with dependencies.

        All parameters are REQUIRED. The orchestrator will fail immediately if any
        required dependency is missing or used incorrectly.

        Args:
            llm_provider: LLM provider for generating responses (required)
            continuum_repo: Repository for message persistence (required)
            working_memory: Working memory system for prompt composition (required)
            tool_repo: Tool repository for tool definitions (required)
            tag_parser: Tag parser for response parsing (required)
            fingerprint_generator: Fingerprint generator for retrieval query expansion (required).
                                  Raises RuntimeError on generation failures - no degraded state.
            event_bus: Event bus for publishing/subscribing to events (required)
            memory_relevance_service: Memory relevance service for surfacing long-term memories (required).
                                     Raises exceptions on infrastructure failures - no degraded state.
        """
        self.llm_provider = llm_provider
        self.continuum_repo = continuum_repo
        self.working_memory = working_memory
        self.tool_repo = tool_repo
        self.tag_parser = tag_parser
        self.fingerprint_generator = fingerprint_generator
        self.memory_relevance_service = memory_relevance_service
        self.event_bus = event_bus

        # Get singleton embeddings provider for generating embeddings once
        self.embeddings_provider = get_hybrid_embeddings_provider()

        # Store composed prompt sections when received via event
        self._cached_content = None
        self._non_cached_content = None

        # Subscribe to system prompt composed event
        self.event_bus.subscribe('SystemPromptComposedEvent', self._handle_system_prompt_composed)

        logger.info("ContinuumOrchestrator initialized")

    def process_message(
        self,
        continuum: Continuum,
        user_message: Union[str, List[Dict[str, Any]]],
        system_prompt: str,
        stream: bool = False,
        stream_callback=None,
        _tried_loading_all_tools: bool = False,
        unit_of_work=None
    ) -> tuple[Continuum, str, Dict[str, Any]]:
        """
        Process user message through complete continuum flow.

        Args:
            continuum: Current continuum state
            user_message: User's input message (string or multimodal content array)
            system_prompt: Base system prompt
            stream: Whether to stream response
            stream_callback: Callback for streaming chunks
            _tried_loading_all_tools: Internal flag to prevent infinite need_tool loops
            unit_of_work: Optional UnitOfWork for batching persistence operations

        Returns:
            Tuple of (updated_continuum, final_response, metadata)
        """
        # Initialize metadata collection
        metadata = {
            "tools_used": [],
            "referenced_memories": []
        }
        
        # Add user message to continuum cache (no persistence yet)
        user_msg_obj, user_events = continuum.add_user_message(user_message)
        self._publish_events(user_events)
        
        # Extract text content for weighted context (bypass for multimodal)
        # For multimodal content, we only use the text portion for embeddings
        text_for_context = user_message
        if isinstance(user_message, list):
            # Extract text from multimodal content array
            text_parts = [item['text'] for item in user_message if item.get('type') == 'text']
            text_for_context = ' '.join(text_parts) if text_parts else 'Image uploaded'

        # Get previous memories from trinket for retention evaluation
        previous_memories = self._get_previous_memories()

        # Generate fingerprint and evaluate retention of previous memories
        # The fingerprint expands fragmentary queries into retrieval-optimized specifics.
        # Retention evaluation uses LLM reasoning to decide which previous memories
        # should stay in context based on conversation trajectory.
        #
        # generate_fingerprint() raises RuntimeError on failure - no degraded state
        fingerprint, retained_texts = self.fingerprint_generator.generate_fingerprint(
            continuum,
            text_for_context,
            previous_memories=previous_memories
        )

        # Apply retention to get pinned memories
        pinned_memories = self._apply_retention(previous_memories, retained_texts)

        # Generate 768d embedding for the fingerprint (query encoding)
        fingerprint_embedding = self.embeddings_provider.encode_realtime(fingerprint)

        # Fresh retrieval with limit of 20
        # Memory service raises exceptions on infrastructure failures - no hedging
        fresh_memories = self.memory_relevance_service.get_relevant_memories(
            fingerprint=fingerprint,
            fingerprint_embedding=fingerprint_embedding,
            limit=20
        )

        # Merge pinned + fresh, deduplicating by memory ID
        # Pinned memories take precedence (appear first)
        surfaced_memories = self._merge_memories(pinned_memories, fresh_memories)

        # Log retrieval for quality evaluation
        from cns.services.retrieval_logger import get_retrieval_logger
        get_retrieval_logger().log_retrieval(
            continuum_id=continuum.id,
            raw_query=text_for_context,
            fingerprint=fingerprint,
            surfaced_memories=surfaced_memories
        )

        logger.info(
            f"Memory surfacing: {len(pinned_memories)} pinned + "
            f"{len(fresh_memories)} fresh = {len(surfaced_memories)} total"
        )

        # Send merged memories to ProactiveMemoryTrinket
        from cns.core.events import UpdateTrinketEvent
        self.event_bus.publish(UpdateTrinketEvent.create(
            continuum_id=str(continuum.id),
            target_trinket="ProactiveMemoryTrinket",
            context={"memories": surfaced_memories}
        ))

        # Now compose system prompt with all context ready
        from cns.core.events import ComposeSystemPromptEvent
        # Reset and wait for synchronous event handler to populate
        self._cached_content = None
        self._non_cached_content = None
        self.event_bus.publish(ComposeSystemPromptEvent.create(
            continuum_id=str(continuum.id),
            base_prompt=system_prompt
        ))
        # Since events are synchronous, content should be ready
        cached_content = self._cached_content or ""
        non_cached_content = self._non_cached_content or ""
        
        # Get available tools - only currently enabled tools
        # With invokeother_tool, the LLM can see all available tools in working memory
        # and load what it needs on demand
        available_tools = self.tool_repo.get_all_tool_definitions()
        
        # Build messages from continuum
        messages = continuum.get_messages_for_api()

        # Build structured system content with cache breakpoints
        system_blocks = []

        # Block 1: Cached content (base prompt + cached trinkets)
        if cached_content:
            system_blocks.append({
                "type": "text",
                "text": cached_content,
                "cache_control": {"type": "ephemeral"}
            })

        # Block 2: Non-cached content (trinkets + temporal)
        dynamic_parts = []
        if non_cached_content:
            dynamic_parts.append(non_cached_content)

        # Add non-cached content block if any exists
        if dynamic_parts:
            system_blocks.append({
                "type": "text",
                "text": "\n\n".join(dynamic_parts)
                # No cache_control - don't cache dynamic content
            })

        # Pass structured system content
        complete_messages = [{"role": "system", "content": system_blocks}] + messages

        # Process through streaming events API
        events = []
        response_text = ""
        raw_response = None
        invoked_tool_loader = False  # Track if invokeother_tool was called during this turn

        # Apply thinking budget preference if set
        llm_kwargs = {}
        thinking_pref = continuum.thinking_budget_preference
        if thinking_pref is not None:
            if thinking_pref == 0:
                # Explicit disable
                llm_kwargs['thinking_enabled'] = False
            else:
                # Explicit enable with budget
                llm_kwargs['thinking_enabled'] = True
                llm_kwargs['thinking_budget'] = thinking_pref

        # Apply model preference if set
        model_pref = continuum.model_preference
        if model_pref is not None:
            llm_kwargs['model_preference'] = model_pref

        # Collect events from generator
        for event in self.llm_provider.stream_events(
            messages=complete_messages,
            tools=available_tools,
            **llm_kwargs
        ):
            from cns.core.stream_events import TextEvent, ThinkingEvent, CompleteEvent, ToolExecutingEvent

            # Detect invokeother_tool execution for auto-continuation
            # Must check here because final response won't contain intermediate tool calls
            if isinstance(event, ToolExecutingEvent):
                if event.tool_name == "invokeother_tool":
                    mode = event.arguments.get("mode", "")
                    if mode in ["load", "fallback"]:
                        invoked_tool_loader = True
                        logger.info(f"Detected invokeother_tool execution with mode={mode}")

            # Call stream callback if provided (for compatibility during transition)
            if stream and stream_callback:
                if isinstance(event, TextEvent):
                    stream_callback({"type": "text", "content": event.content})
                    response_text += event.content
                elif isinstance(event, ThinkingEvent):
                    stream_callback({"type": "thinking", "content": event.content})
                elif hasattr(event, 'tool_name'):
                    stream_callback({"type": "tool_event", "event": event.type, "tool": event.tool_name})

            # Store events for websocket
            events.append(event)

            # Capture final response
            if isinstance(event, CompleteEvent):
                raw_response = event.response
                response_text = self.llm_provider.extract_text_content(raw_response)

                # Log cache metrics for observability
                if hasattr(raw_response, 'usage') and raw_response.usage:
                    usage = raw_response.usage
                    cache_created = getattr(usage, 'cache_creation_input_tokens', 0)
                    cache_read = getattr(usage, 'cache_read_input_tokens', 0)
                    if cache_created > 0:
                        logger.info(f"Cache created: {cache_created} tokens")
                    if cache_read > 0:
                        logger.debug(f"Cache read: {cache_read} tokens")
        
        # Extract tools used from LLM response for metadata
        # Note: invoked_tool_loader is already set from ToolExecutingEvent above
        tool_calls = self.llm_provider.extract_tool_calls(raw_response)
        if tool_calls:
            tools_used_this_turn = [call["tool_name"] for call in tool_calls]
            metadata["tools_used"] = tools_used_this_turn

        # Parse tags from final response (preserve emotion tag for frontend extraction)
        parsed_tags = self.tag_parser.parse_response(response_text, preserve_tags=['my_emotion'])
        clean_response_text = parsed_tags['clean_text']

        # Debug: Log emotion tag presence
        logger.info(f"Emotion extracted: {parsed_tags.get('emotion')}")
        logger.info(f"Emotion tag in clean_text: {'<mira:my_emotion>' in clean_response_text}")

        # Add final assistant response to continuum FIRST (before topic change handling)
        # Validate response is not blank before saving
        if not clean_response_text or not clean_response_text.strip():
            logger.error("Attempted to save blank assistant response - rejecting")
            raise ValueError("Assistant response cannot be blank or empty. This may indicate an API error.")

        assistant_metadata = {
            "referenced_memories": parsed_tags.get('referenced_memories', []),
            "surfaced_memories": [m['id'] for m in surfaced_memories]  # Add surfaced memory IDs
        }

        # Add emotion if present
        if parsed_tags.get('emotion'):
            assistant_metadata["emotion"] = parsed_tags['emotion']

        assistant_msg_obj, response_events = continuum.add_assistant_message(
            clean_response_text, assistant_metadata
        )
        self._publish_events(response_events)

        # Publish turn completed event for subscribers (Letta buffering, tool auto-unload, etc.)
        # Pass continuum object so handlers can extract whatever data they need
        # Calculate turn number from message count (each turn = user msg + assistant msg)
        turn_number = (len(continuum.messages) + 1) // 2
        self._publish_events([TurnCompletedEvent.create(
            continuum_id=str(continuum.id),
            turn_number=turn_number,
            continuum=continuum
        )])

        final_response = clean_response_text
        
        # Update metadata with referenced memories
        metadata["referenced_memories"] = parsed_tags.get('referenced_memories', [])
        metadata["surfaced_memories"] = [m['id'] for m in surfaced_memories]

        # Add emotion to metadata for persistence
        if parsed_tags.get('emotion'):
            metadata["emotion"] = parsed_tags['emotion']
        
        # Unit of Work is required for proper persistence
        if not unit_of_work:
            raise ValueError("Unit of Work is required for message persistence")

        # Prepare user message for persistence
        persist_content = user_msg_obj.content
        if isinstance(user_msg_obj.content, list):
            # Extract only text from multimodal content for database storage
            text_parts = [item['text'] for item in user_msg_obj.content if item.get('type') == 'text']
            persist_content = ' '.join(text_parts) if text_parts else str(user_msg_obj.content)

        # Create a copy with text-only content for persistence if needed
        from cns.core.message import Message
        persist_user_msg = Message(
            content=persist_content,
            role=user_msg_obj.role,
            id=user_msg_obj.id,
            created_at=user_msg_obj.created_at,
            metadata=user_msg_obj.metadata
        )

        # Add messages to unit of work for batch persistence
        unit_of_work.add_messages(persist_user_msg, assistant_msg_obj)

        # Mark metadata for update
        unit_of_work.mark_metadata_updated()

        # Auto-continuation: If tools were loaded and we haven't already tried,
        # automatically continue with the task
        if invoked_tool_loader and not _tried_loading_all_tools:
            logger.info("Auto-continuing after tool loading...")

            # Create synthetic user message to prompt continuation
            synthetic_message = (
                "Great, the tool is now available. Please proceed with completing "
                "the original task using the newly loaded tool."
            )

            # Recursively process with the synthetic message
            # Pass _tried_loading_all_tools=True to prevent infinite loops
            continuum, final_response, metadata = self.process_message(
                continuum,
                synthetic_message,
                system_prompt,
                stream=stream,
                stream_callback=stream_callback,
                _tried_loading_all_tools=True,  # Prevent infinite loops
                unit_of_work=unit_of_work
            )
            logger.info("Auto-continuation completed successfully")

        return continuum, final_response, metadata
    
    def _handle_system_prompt_composed(self, event):
        """Handle system prompt composed event."""
        from cns.core.events import SystemPromptComposedEvent
        event: SystemPromptComposedEvent
        self._cached_content = event.cached_content
        self._non_cached_content = event.non_cached_content
        logger.debug(f"Received structured system prompt (cached: {len(event.cached_content)} chars, non-cached: {len(event.non_cached_content)} chars)")


    def _publish_events(self, events: List[ContinuumEvent]):
        """Publish events to event bus."""
        for event in events:
            self.event_bus.publish(event)

    def _get_previous_memories(self) -> List[Dict[str, Any]]:
        """
        Get previously surfaced memories from the trinket cache.

        Returns:
            List of memory dicts from the previous turn, or empty list if none
        """
        trinket = self.working_memory.get_trinket('ProactiveMemoryTrinket')
        if trinket and hasattr(trinket, 'get_cached_memories'):
            return trinket.get_cached_memories()
        return []

    def _apply_retention(
        self,
        previous_memories: List[Dict[str, Any]],
        retained_texts: set
    ) -> List[Dict[str, Any]]:
        """
        Filter previous memories to keep only those marked for retention.

        Matches by memory text since the LLM outputs full text, not IDs.

        Args:
            previous_memories: All memories from previous turn
            retained_texts: Set of memory texts marked [x] by the LLM

        Returns:
            List of memories that should be pinned (retained)
        """
        if not previous_memories or not retained_texts:
            return []

        pinned = []
        for memory in previous_memories:
            memory_text = memory.get('text', '')
            if memory_text and memory_text in retained_texts:
                pinned.append(memory)

        logger.debug(
            f"Retention: {len(pinned)}/{len(previous_memories)} memories retained"
        )
        return pinned

    def _merge_memories(
        self,
        pinned_memories: List[Dict[str, Any]],
        fresh_memories: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Merge pinned and fresh memories, deduplicating by ID.

        Pinned memories appear first and take precedence.

        Args:
            pinned_memories: Memories retained from previous turn
            fresh_memories: Newly retrieved memories

        Returns:
            Merged list with pinned first, then fresh (no duplicates)
        """
        # Start with pinned memories
        merged = list(pinned_memories)
        seen_ids = {m.get('id') for m in pinned_memories if m.get('id')}

        # Add fresh memories that aren't already in pinned
        for memory in fresh_memories:
            memory_id = memory.get('id')
            if memory_id and memory_id not in seen_ids:
                merged.append(memory)
                seen_ids.add(memory_id)

        return merged


# Global orchestrator instance (singleton pattern)
_orchestrator_instance = None


def initialize_orchestrator(orchestrator_instance: ContinuumOrchestrator) -> None:
    """
    Initialize the global orchestrator instance.
    
    This should be called once during application startup after creating
    the orchestrator with all its dependencies.
    
    Args:
        orchestrator_instance: The configured ConversationOrchestrator instance
    """
    global _orchestrator_instance
    if _orchestrator_instance is not None:
        logger.warning("Orchestrator already initialized, replacing existing instance")
    _orchestrator_instance = orchestrator_instance
    logger.info("Global orchestrator instance initialized")


def get_orchestrator() -> ContinuumOrchestrator:
    """
    Get the global orchestrator instance.
    
    Returns:
        The singleton ConversationOrchestrator instance
        
    Raises:
        RuntimeError: If orchestrator has not been initialized
    """
    global _orchestrator_instance
    if _orchestrator_instance is None:
        raise RuntimeError(
            "Orchestrator not initialized. Ensure initialize_orchestrator() "
            "is called during application startup."
        )
    return _orchestrator_instance
