"""
Memory Relevance Service - CNS Integration Point for LT_Memory

Provides the primary interface for the CNS orchestrator to interact with
the long-term memory system. Wraps ProactiveService from lt_memory to follow
CNS service patterns while aligning with the new lt_memory architecture.

CNS Integration Points:
- get_relevant_memories(embedded_msg) -> List[Dict] of memory data
- Uses pre-computed embeddings from EmbeddedMessage (no redundant embedding generation)
- Returns hierarchical memory structures with link metadata

Architecture:
- Thin wrapper around lt_memory.proactive.ProactiveService
- Delegates all heavy lifting to ProactiveService
- Handles CNS-specific formatting and error handling
"""
import logging
from typing import List, Dict, Any

from cns.core.embedded_message import EmbeddedMessage
from lt_memory.proactive import ProactiveService

logger = logging.getLogger(__name__)


class MemoryRelevanceService:
    """
    CNS service for memory relevance scoring (parallel to ToolRelevanceService).

    Wraps the lt_memory ProactiveService to provide memory surfacing for continuums.
    Uses pre-computed embeddings from CNS to avoid redundant embedding generation.

    CNS Integration:
    - Receives EmbeddedMessage with 384-dim AllMiniLM embedding already computed
    - Returns list of memory dicts with hierarchical link structure
    - Handles errors gracefully to prevent continuum failures
    """

    def __init__(self, proactive_service: ProactiveService):
        """
        Initialize memory relevance service.

        Args:
            proactive_service: lt_memory ProactiveService instance (from factory)
        """
        self.proactive = proactive_service
        logger.info("MemoryRelevanceService initialized with ProactiveService")

    def get_relevant_memories(
        self,
        embedded_msg: EmbeddedMessage,
        touchstone: Dict[str, Any],
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get memories relevant to embedded message.

        This is the primary CNS integration point. Called by ConversationOrchestrator
        during message processing to surface relevant long-term memories.

        Args:
            embedded_msg: EmbeddedMessage with pre-computed embedding_384
            limit: Maximum memories to return (default: 10)

        Returns:
            List of memory dicts with hierarchical structure:
            [
                {
                    "id": "uuid",
                    "text": "memory text",
                    "importance_score": 0.85,
                    "created_at": "iso-timestamp",
                    "linked_memories": [
                        {
                            "id": "uuid",
                            "text": "linked memory text",
                            "link_metadata": {
                                "link_type": "conflicts",
                                "confidence": 0.91,
                                "reasoning": "...",
                                "depth": 1
                            },
                            "linked_memories": [...]
                        }
                    ]
                }
            ]

        Raises:
            ValueError: If embedded message validation fails
            RuntimeError: If memory service infrastructure fails
        """
        # Validate embedded message has the required embedding
        if not hasattr(embedded_msg, 'embedding_384') or embedded_msg.embedding_384 is None:
            raise ValueError("EmbeddedMessage missing embedding_384 - cannot surface memories")

        # Delegate to ProactiveService with pre-computed embedding
        # Use weighted_context for reranking - it contains the rich touchstone-based context
        memories = self.proactive.search_with_embedding(
            embedding=embedded_msg.embedding_384,
            touchstone=touchstone,
            query_text=embedded_msg.weighted_context,
            limit=limit
        )

        if memories:
            logger.info(f"Surfaced {len(memories)} relevant memories for user {embedded_msg.user_id}")
        else:
            logger.debug(f"No relevant memories found for user {embedded_msg.user_id}")

        return memories

    def cleanup(self):
        """Clean up resources."""
        self.proactive = None
        logger.debug("MemoryRelevanceService cleanup completed")
