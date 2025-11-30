"""
Memory Relevance Service - CNS Integration Point for LT_Memory

Provides the primary interface for the CNS orchestrator to interact with
the long-term memory system. Wraps ProactiveService from lt_memory.

CNS Integration Points:
- get_relevant_memories(fingerprint, fingerprint_embedding) -> List[Dict]
- Uses pre-computed 768d embeddings (no redundant embedding generation)
- Returns hierarchical memory structures with link metadata
"""
import logging
from typing import List, Dict, Any
import numpy as np

from lt_memory.proactive import ProactiveService

logger = logging.getLogger(__name__)


class MemoryRelevanceService:
    """
    CNS service for memory relevance scoring.

    Wraps the lt_memory ProactiveService to provide memory surfacing for continuums.
    Uses pre-computed 768d fingerprint embeddings from CNS.
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
        fingerprint: str,
        fingerprint_embedding: np.ndarray,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get memories relevant to the fingerprint.

        Args:
            fingerprint: Expanded memory fingerprint (retrieval-optimized query)
            fingerprint_embedding: Pre-computed 768d embedding of fingerprint
            limit: Maximum memories to return (default: 10)

        Returns:
            List of memory dicts with hierarchical structure:
            [
                {
                    "id": "uuid",
                    "text": "memory text",
                    "importance_score": 0.85,
                    "similarity_score": 0.82,
                    "created_at": "iso-timestamp",
                    "linked_memories": [...]
                }
            ]

        Raises:
            ValueError: If fingerprint embedding validation fails
            RuntimeError: If memory service infrastructure fails
        """
        # Validate embedding
        if fingerprint_embedding is None:
            raise ValueError("fingerprint_embedding is required")

        if len(fingerprint_embedding) != 768:
            raise ValueError(f"Expected 768d embedding, got {len(fingerprint_embedding)}d")

        # Delegate to ProactiveService
        memories = self.proactive.search_with_embedding(
            embedding=fingerprint_embedding,
            fingerprint=fingerprint,
            limit=limit
        )

        if memories:
            logger.info(f"Surfaced {len(memories)} relevant memories")
        else:
            logger.debug("No relevant memories found")

        return memories

    def cleanup(self):
        """Clean up resources."""
        self.proactive = None
        logger.debug("MemoryRelevanceService cleanup completed")
