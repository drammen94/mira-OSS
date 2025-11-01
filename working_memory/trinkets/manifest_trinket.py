"""Manifest trinket for displaying conversation segment manifest in system prompt."""
import logging
from typing import Dict, Any

from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class ManifestTrinket(EventAwareTrinket):
    """
    Displays conversation manifest in working memory.

    This trinket formats the segment-based conversation manifest
    into a structured section for the system prompt, showing recent
    conversation segments organized by time.
    """

    # Manifest is cacheable (changes infrequently)
    cache_policy = True

    def __init__(self, event_bus, working_memory):
        """Initialize manifest trinket with event bus and required service."""
        super().__init__(event_bus, working_memory)
        # Initialize service immediately - fail at startup if misconfigured
        from cns.services.manifest_query_service import get_manifest_query_service
        self._manifest_service = get_manifest_query_service()

    def _get_variable_name(self) -> str:
        """Manifest publishes to 'conversation_manifest'."""
        return "conversation_manifest"

    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate manifest content from segment boundaries.

        Args:
            context: Update context containing 'user_id'

        Returns:
            Formatted manifest section or empty string if no segments

        Raises:
            DatabaseError: If database query fails (infrastructure failure)
        """
        user_id = context.get('user_id')
        if not user_id:
            logger.warning("ManifestTrinket called without user_id in context")
            return ""

        # Let infrastructure failures propagate
        manifest = self._manifest_service.get_manifest_for_prompt(user_id)

        if not manifest:
            logger.debug("No segments available for manifest")
            return ""  # Legitimately empty - no conversation history yet

        logger.debug(f"Generated manifest for user {user_id}")
        return manifest
