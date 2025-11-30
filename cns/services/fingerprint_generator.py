"""
Fingerprint generator service for retrieval-optimized query expansion.

Transforms fragmentary user queries into detailed, specific queries
optimized for finding relevant memories via embedding similarity.

Key principle: The fingerprint REPLACES the original query for retrieval,
rather than augmenting it. Research showed this approach outperforms
query augmentation for personal memory search.

Also handles memory retention decisions - evaluating which previously
surfaced memories should remain in context based on conversation trajectory.
"""
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Set, Optional

from config.config import ApiConfig
from cns.core.continuum import Continuum
from clients.vault_client import get_api_key

logger = logging.getLogger(__name__)

# Number of user/assistant pairs to include as context (6 pairs = 12 messages)
CONTEXT_PAIRS = 6


class FingerprintGenerator:
    """
    Generates retrieval-optimized memory fingerprints from user messages.

    Uses a fast model (Groq) to expand fragmentary queries into detailed
    specifics that match stored memory vocabulary for better embedding similarity.
    """

    def __init__(self, config: ApiConfig, llm_provider):
        """
        Initialize fingerprint generator.

        Args:
            config: API configuration with LLM settings
            llm_provider: LLM provider for fingerprint generation calls

        Raises:
            FileNotFoundError: If prompt files not found
            ValueError: If API key not found in Vault
            RuntimeError: If fingerprint generation is disabled
        """
        self.config = config
        self.llm_provider = llm_provider

        if not config.analysis_enabled:
            raise RuntimeError(
                "FingerprintGenerator requires analysis_enabled=True in configuration"
            )

        # Load prompt templates
        system_prompt_path = Path("config/prompts/fingerprint_expansion_system.txt")
        user_prompt_path = Path("config/prompts/fingerprint_expansion_user.txt")

        if not system_prompt_path.exists():
            raise FileNotFoundError(
                f"Fingerprint system prompt not found at {system_prompt_path}"
            )

        if not user_prompt_path.exists():
            raise FileNotFoundError(
                f"Fingerprint user prompt not found at {user_prompt_path}"
            )

        with open(system_prompt_path, 'r') as f:
            self.system_prompt = f.read()

        with open(user_prompt_path, 'r') as f:
            self.user_prompt_template = f.read()

        # Get API key for LLM endpoint
        self.api_key = get_api_key(config.analysis_api_key_name)
        if not self.api_key:
            raise ValueError(
                f"API key '{config.analysis_api_key_name}' not found in Vault"
            )

        logger.info(
            f"FingerprintGenerator initialized: {config.analysis_model} @ "
            f"{config.analysis_endpoint}"
        )

    def generate_fingerprint(
        self,
        continuum: Continuum,
        current_user_message: str,
        previous_memories: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[str, Set[str]]:
        """
        Generate retrieval-optimized memory fingerprint and evaluate retention.

        Expands fragmentary queries into detailed specifics:
        - Resolves "that", "it", "the one" to concrete references
        - Expands implicit context to explicit names, places, dates
        - Outputs vocabulary that matches stored memories

        Also evaluates which previously surfaced memories should remain in context.

        Args:
            continuum: Current continuum with message history
            current_user_message: User message to expand
            previous_memories: List of memory dicts from previous turn (optional)

        Returns:
            Tuple of (fingerprint, retained_texts):
            - fingerprint: Expanded query string for embedding and retrieval
            - retained_texts: Set of memory texts marked for retention ([x])

        Raises:
            RuntimeError: On generation failure
        """
        try:
            conversation_turns = self._format_recent_turns(
                continuum,
                current_user_message
            )

            # Format previous memories if provided
            memories_block = self._format_previous_memories(previous_memories)

            user_message = self.user_prompt_template.replace(
                "{conversation_turns}",
                conversation_turns
            ).replace(
                "{user_message}",
                current_user_message
            ).replace(
                "{previous_memories}",
                memories_block
            )

            logger.debug(f"Generating fingerprint for: {current_user_message[:100]}...")
            if previous_memories:
                logger.debug(f"Evaluating retention for {len(previous_memories)} memories")

            response = self.llm_provider.generate_response(
                messages=[{"role": "user", "content": user_message}],
                stream=False,
                endpoint_url=self.config.analysis_endpoint,
                model_override=self.config.analysis_model,
                api_key_override=self.api_key,
                system_override=self.system_prompt
            )

            response_text = self.llm_provider.extract_text_content(response).strip()

            if not response_text:
                raise RuntimeError("Fingerprint generation returned empty response")

            # Parse fingerprint and retention from response
            fingerprint, retained_texts = self._parse_response(
                response_text,
                previous_memories
            )

            logger.info(f"Generated fingerprint: {fingerprint[:150]}...")
            if previous_memories:
                logger.info(
                    f"Retention: {len(retained_texts)}/{len(previous_memories)} memories retained"
                )

            return fingerprint, retained_texts

        except Exception as e:
            logger.error(f"Fingerprint generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Fingerprint generation failed: {str(e)}") from e

    def _format_previous_memories(
        self,
        memories: Optional[List[Dict[str, Any]]]
    ) -> str:
        """
        Format previous memories for the prompt.

        Args:
            memories: List of memory dicts with 'text' field

        Returns:
            Formatted memory block or empty string if no memories
        """
        if not memories:
            return ""

        lines = ["\n<previous_memories>"]
        for memory in memories:
            text = memory.get('text', '')
            if text:
                lines.append(text)
        lines.append("</previous_memories>\n")

        return "\n".join(lines)

    def _parse_response(
        self,
        response_text: str,
        previous_memories: Optional[List[Dict[str, Any]]]
    ) -> Tuple[str, Set[str]]:
        """
        Parse fingerprint and retention decisions from LLM response.

        Args:
            response_text: Raw LLM response
            previous_memories: Original memories for fallback matching

        Returns:
            Tuple of (fingerprint, retained_texts)
        """
        # Extract fingerprint from <fingerprint> tags
        fingerprint_match = re.search(
            r'<fingerprint>(.*?)</fingerprint>',
            response_text,
            re.DOTALL
        )
        if fingerprint_match:
            fingerprint = fingerprint_match.group(1).strip()
        else:
            # Fallback: if no tags, treat entire response as fingerprint (backward compat)
            fingerprint = response_text.strip()
            # Remove any retention block if present
            fingerprint = re.sub(
                r'<memory_retention>.*?</memory_retention>',
                '',
                fingerprint,
                flags=re.DOTALL
            ).strip()

        if not fingerprint:
            raise RuntimeError("Failed to extract fingerprint from response")

        # Extract retained memories from <memory_retention> tags
        retained_texts: Set[str] = set()

        if not previous_memories:
            return fingerprint, retained_texts

        retention_match = re.search(
            r'<memory_retention>(.*?)</memory_retention>',
            response_text,
            re.DOTALL
        )

        if retention_match:
            retention_block = retention_match.group(1)
            # Find all lines starting with [x] (retained)
            retained_lines = re.findall(r'\[x\]\s*(.+?)(?=\n|$)', retention_block)
            retained_texts = {line.strip() for line in retained_lines}
            logger.debug(f"Parsed {len(retained_texts)} retained memories from response")
        else:
            # Parse failure fallback: keep all previous memories (conservative)
            logger.warning(
                "No <memory_retention> block found in response, keeping all memories"
            )
            retained_texts = {m.get('text', '') for m in previous_memories if m.get('text')}

        return fingerprint, retained_texts

    def _format_recent_turns(
        self,
        continuum: Continuum,
        current_user_message: str
    ) -> str:
        """
        Format recent conversation turns for context.

        Skips collapsed segment summaries to only include actual conversation pairs.

        Args:
            continuum: Continuum with message cache
            current_user_message: Current user message

        Returns:
            Formatted string with last 6 pairs plus current message
        """
        lines = []
        pairs_found = 0
        i = len(continuum.messages) - 1

        # Walk backwards to extract user/assistant pairs
        while i >= 0 and pairs_found < CONTEXT_PAIRS:
            # Find assistant message (skip segment summaries)
            while i >= 0:
                msg = continuum.messages[i]
                if msg.role == "assistant" and not self._is_segment_summary(msg):
                    break
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

            # Prepend pair (we're walking backwards)
            user_content = self._extract_text_content(user_msg.content)
            lines.insert(0, f"Assistant: {assistant_msg.content}")
            lines.insert(0, f"User: {user_content}")
            pairs_found += 1

        # Append current user message
        lines.append(f"User: {current_user_message}")

        return "\n".join(lines)

    def _is_segment_summary(self, message) -> bool:
        """Check if message is a collapsed segment summary."""
        metadata = getattr(message, 'metadata', {}) or {}
        return (
            metadata.get('is_segment_boundary', False) and
            metadata.get('status') == 'collapsed'
        )

    def _extract_text_content(self, content) -> str:
        """Extract text from potentially multimodal content."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = [
                item['text'] for item in content
                if isinstance(item, dict) and item.get('type') == 'text'
            ]
            return ' '.join(text_parts) if text_parts else '[non-text content]'

        return str(content)
