"""
GetContext search results trinket.

Displays asynchronous context search results when they become available.
Listens for UpdateTrinketEvent and retrieves results from Valkey.
"""
import json
import logging
from typing import Dict, Any

from working_memory.trinkets.base import EventAwareTrinket
from clients.valkey_client import get_valkey_client
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class GetContextTrinket(EventAwareTrinket):
    """
    Displays context search results from getcontext_tool.

    This trinket receives task IDs via UpdateTrinketEvent,
    retrieves search results from Valkey, and formats them
    for display in the system prompt.
    """

    def __init__(self, event_bus, working_memory):
        """Initialize with Valkey client."""
        super().__init__(event_bus, working_memory)
        self.valkey_client = get_valkey_client()
        self.logger = logger

    def _get_variable_name(self) -> str:
        """GetContext publishes to 'context_search_results'."""
        return "context_search_results"

    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate content when requested via UpdateTrinketEvent.

        Args:
            context: Update context containing 'task_id'

        Returns:
            Formatted search results or empty string if no results
        """
        # Check if this is a context search update
        task_id = context.get('task_id')
        if not task_id:
            return ""

        # Get current user ID
        user_id = get_current_user_id()

        # Retrieve results from Valkey
        results_key = f"context_search:{user_id}:{task_id}"
        results_json = self.valkey_client.get(results_key)

        if not results_json:
            self.logger.debug(f"No results found for task {task_id}")
            return ""

        # Parse results
        results = json.loads(results_json)

        # Clear from cache after retrieval
        self.valkey_client.delete(results_key)

        # Return the summary content directly
        return self._format_search_results(results)

    def _format_search_results(self, results: Dict[str, Any]) -> str:
        """Format search results - just dump the summary content."""
        # Create a simple header
        header = f"📎 Context Search: {results.get('query', 'Unknown query')}"

        # Get the summary content
        summary = results.get('summary', '')

        # Format key findings if present
        findings = []
        for finding in results.get('key_findings', []):
            finding_text = f"• {finding.get('point', '')}"
            if finding.get('source'):
                finding_text += f" (source: {finding['source']})"
            findings.append(finding_text)

        # Combine all parts
        parts = [header]
        if summary:
            parts.append(summary)
        if findings:
            parts.append("\n" + "\n".join(findings))
        if results.get('limitations'):
            parts.append(f"\nNote: {results['limitations']}")

        return "\n\n".join(parts)