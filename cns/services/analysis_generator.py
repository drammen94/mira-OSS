"""
Analysis generator service for pre-processing touchstone generation.

This service runs a fast LLM call before the main response to generate an evolved
semantic touchstone for memory retrieval. Fixes the one-turn-behind problem by
generating the touchstone synchronously before memory systems need it.

FUTURE ENHANCEMENT - Touchstone Embeddings:
    The touchstone is an ideal semantic summary for continuum-level operations.
    Consider adding a vector column to the continuums table:

    ALTER TABLE continuums ADD COLUMN touchstone_embedding vector(384);
    CREATE INDEX ON conversations USING ivfflat (touchstone_embedding vector_cosine_ops);

    This would enable:
    - Continuum similarity search: Find continuums about similar topics/entities
    - Continuum clustering: Group related continuums for the user
    - Cross-continuum memory: "What conversations have I had about X?"
    - Continuum recommendations: Surface related past continuums

    The touchstone already distills the continuum's semantic essence, making it
    superior to raw message embeddings for continuum-level operations.

    Implementation approach:
    1. Generate embedding when touchstone is created (use existing embeddings_provider)
    2. Store alongside touchstone text in continuum metadata update
    3. Build similarity search queries using pgvector
"""
import json
import logging
from typing import Optional
from pathlib import Path

from config.config import ApiConfig
from cns.core.continuum import Continuum
from utils.tag_parser import TagParser
from clients.vault_client import get_api_key

logger = logging.getLogger(__name__)


class AnalysisGenerator:
    """
    Service for generating evolved semantic touchstones via pre-processing LLM call.

    Uses a fast model (llama-3.1-8b-instruct) to analyze recent continuum context
    and produce an updated touchstone that bridges past entities with current focus.
    Stores touchstone in continuum metadata for next-turn evolution.
    """

    def __init__(self, config: ApiConfig, tag_parser: TagParser, llm_provider, embeddings_provider=None):
        """
        Initialize analysis generator.

        Args:
            config: API configuration with analysis settings
            tag_parser: Tag parser for extracting touchstones
            llm_provider: LLM provider for routing analysis calls
            embeddings_provider: Optional embeddings provider for generating touchstone embeddings

        Raises:
            FileNotFoundError: If prompt files not found
            ValueError: If analysis API key not found in Vault
            RuntimeError: If analysis is disabled in configuration
        """
        self.config = config
        self.tag_parser = tag_parser
        self.llm_provider = llm_provider

        # Analysis must be enabled in configuration
        if not config.analysis_enabled:
            raise RuntimeError("AnalysisGenerator is disabled in configuration - analysis_enabled must be True")

        # Get embeddings provider for touchstone embeddings
        if embeddings_provider is None:
            from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider
            self.embeddings_provider = get_hybrid_embeddings_provider()
        else:
            self.embeddings_provider = embeddings_provider

        # Load system and user prompt templates - required
        system_prompt_path = Path("config/prompts/analysis_generation_system.txt")
        user_prompt_path = Path("config/prompts/analysis_generation_user.txt")

        if not system_prompt_path.exists():
            raise FileNotFoundError(f"Analysis system prompt not found at {system_prompt_path}")

        if not user_prompt_path.exists():
            raise FileNotFoundError(f"Analysis user prompt not found at {user_prompt_path}")

        with open(system_prompt_path, 'r') as f:
            self.system_prompt_template = f.read()
        logger.debug(f"Loaded analysis system prompt from {system_prompt_path}")

        with open(user_prompt_path, 'r') as f:
            self.user_prompt_template = f.read()
        logger.debug(f"Loaded analysis user prompt from {user_prompt_path}")

        # Get API key for analysis endpoint - required
        self.api_key = get_api_key(config.analysis_api_key_name)
        if not self.api_key:
            raise ValueError(f"Analysis API key '{config.analysis_api_key_name}' not found in Vault - analysis generation requires valid credentials")

        logger.info(f"AnalysisGenerator initialized: {config.analysis_model} @ {config.analysis_endpoint}")

    def generate_analysis(
        self,
        continuum: Continuum,
        current_user_message: str
    ) -> dict:
        """
        Generate evolved touchstone from continuum context.

        Extracts the previous touchstone from continuum metadata, builds context
        from recent message pairs, and calls the analysis model to produce an updated
        touchstone. Stores the new touchstone in continuum metadata.

        Args:
            continuum: Current continuum object with message history (in-memory cache)
            current_user_message: New user message being processed

        Returns:
            Touchstone dict with narrative, temporal_context, relationship_context, entities.
            Never returns None - raises RuntimeError on any failure.

        Raises:
            RuntimeError: If analysis model returns empty response, malformed JSON, missing fields,
                         or any other infrastructure failure
        """
        try:
            # Extract previous touchstone narrative from continuum metadata
            previous_touchstone = continuum.last_touchstone
            if previous_touchstone and isinstance(previous_touchstone, dict):
                previous_narrative = previous_touchstone.get('narrative', 'None - this is the first exchange')
            else:
                previous_narrative = "None - this is the first exchange"

            # Build continuum context from recent message pairs
            context_messages = self._build_context_messages(
                continuum,
                current_user_message
            )

            # Format conversation turns
            conversation_turns = self._format_continuum_context(context_messages)

            # Build user message from template
            user_message = self.user_prompt_template.replace(
                "{previous_narrative}",
                previous_narrative
            ).replace(
                "{conversation_turns}",
                conversation_turns
            )

            # Call analysis model via LLMProvider with overrides
            logger.debug(f"Calling analysis model with {len(context_messages)} message pairs")
            response = self.llm_provider.generate_response(
                messages=[{"role": "user", "content": user_message}],
                stream=False,
                endpoint_url=self.config.analysis_endpoint,
                model_override=self.config.analysis_model,
                api_key_override=self.api_key,
                system_override=self.system_prompt_template
            )

            # Extract response text (no tag parsing needed - dedicated endpoint returns raw touchstone)
            touchstone_text = self.llm_provider.extract_text_content(response).strip()

            if not touchstone_text:
                raise RuntimeError("Analysis model returned empty response - no text content in response")

            # Strip markdown code fences if present (LLMs often wrap JSON in ```json ... ```)
            if touchstone_text.startswith("```"):
                try:
                    first_newline = touchstone_text.index('\n')
                    last_fence = touchstone_text.rfind("```")
                    if last_fence > first_newline:
                        touchstone_text = touchstone_text[first_newline+1:last_fence].strip()
                        logger.debug("Stripped markdown code fences from touchstone response")
                except ValueError:
                    # No newline found - try to strip without it
                    touchstone_text = touchstone_text.replace("```json", "").replace("```", "").strip()
                    logger.debug("Stripped malformed markdown fences from touchstone response")

            # Parse JSON response with repair fallback
            try:
                touchstone = json.loads(touchstone_text)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed touchstone JSON: {e}")
                logger.debug(f"Response text (first 500 chars): {touchstone_text[:500]}")

                # Attempt repair using json_repair
                try:
                    from json_repair import repair_json
                    repaired = repair_json(touchstone_text)
                    touchstone = json.loads(repaired)
                    logger.info("Successfully repaired malformed touchstone JSON")
                except ImportError:
                    raise RuntimeError("json_repair module not available - cannot parse malformed touchstone JSON. This is a required dependency for analysis generation.")
                except Exception as repair_error:
                    raise RuntimeError(f"Failed to repair and parse touchstone JSON: {repair_error}") from repair_error

            # Validate required fields exist
            required_fields = ['narrative', 'relationship_context', 'entities']
            missing_fields = [field for field in required_fields if field not in touchstone]
            if missing_fields:
                raise RuntimeError(f"Analysis model returned touchstone with missing required fields: {missing_fields}. Received: {touchstone}")

            # Build embedding text from structured components
            embedding_text = f"{touchstone['narrative']} {touchstone['relationship_context']} {touchstone['entities']}"

            logger.info(f"Generated structured touchstone with narrative: {touchstone['narrative'][:100]}...")

            # Generate embedding for touchstone (384-dim for fast operations)
            touchstone_embedding = self.embeddings_provider.encode_realtime(embedding_text)
            logger.debug(f"Generated touchstone embedding: {len(touchstone_embedding)}-dim")

            # Convert ndarray to list for JSON serialization (storage boundary)
            embedding_list = touchstone_embedding.tolist()

            # Store touchstone and embedding in continuum metadata
            continuum.set_last_touchstone(touchstone, embedding_list)

            return touchstone

        except Exception as e:
            logger.error(f"Analysis generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Touchstone generation failed: {str(e)}") from e

    def _build_context_messages(
        self,
        continuum: Continuum,
        current_user_message: str
    ) -> list:
        """
        Build list of recent user/assistant turn pairs plus current message.

        Extracts complete conversational turns (user → assistant pairs),
        skipping all tool messages to provide clean context without execution noise.

        Uses in-memory continuum cache - no DB query.

        Args:
            continuum: Continuum object with populated message cache
            current_user_message: Current user message being processed

        Returns:
            List of message dicts with role and content
        """
        max_pairs = self.config.analysis_context_pairs

        # Extract complete user/assistant pairs by walking backwards
        pairs = []
        i = len(continuum.messages) - 1

        while i >= 0 and len(pairs) < max_pairs:
            # Find assistant message
            while i >= 0 and continuum.messages[i].role != "assistant":
                i -= 1
            if i < 0:
                break
            assistant_msg = continuum.messages[i]
            i -= 1

            # Find preceding user message
            while i >= 0 and continuum.messages[i].role != "user":
                i -= 1
            if i < 0:
                break
            user_msg = continuum.messages[i]
            i -= 1

            # Add pair (we're going backwards, so prepend)
            pairs.insert(0, {"role": "user", "content": user_msg.content})
            pairs.insert(1, {"role": "assistant", "content": assistant_msg.content})

        # Append current user message
        pairs.append({"role": "user", "content": current_user_message})

        return pairs

    def _format_continuum_context(self, messages: list) -> str:
        """
        Format message list into readable continuum turns.

        Args:
            messages: List of message dicts

        Returns:
            Formatted string with continuum turns
        """
        lines = []

        for msg in messages:
            role = msg["role"].capitalize()
            content = msg["content"]

            # Clean content for display (remove old analysis tags if present)
            if msg["role"] == "assistant":
                parsed = self.tag_parser.parse_response(content)
                content = parsed.get('clean_text', content)

            lines.append(f"{role}: {content}")

        return "\n".join(lines)
