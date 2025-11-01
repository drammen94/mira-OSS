"""
Proactive memory surfacing for CNS integration.

Provides intelligent memory search using pre-computed embeddings and
automatic inclusion of linked memories for context enrichment.
"""
import logging
from typing import List, Dict, Any, Optional, Union
import numpy as np

from config.config import ProactiveConfig
from lt_memory.db_access import LTMemoryDB
from lt_memory.linking import LinkingService
from lt_memory.vector_ops import VectorOps

logger = logging.getLogger(__name__)


class ProactiveService:
    """
    Service for proactive memory surfacing in conversations.

    Finds relevant memories using pre-computed embeddings from CNS and
    automatically includes linked memories for richer context.
    """

    def __init__(
        self,
        config: ProactiveConfig,
        vector_ops: VectorOps,
        linking_service: LinkingService,
        db: LTMemoryDB
    ):
        """
        Initialize proactive service.

        Args:
            config: Proactive surfacing configuration
            vector_ops: Vector operations service
            linking_service: Memory linking service
            db: Database access layer
        """
        self.config = config
        self.vector_ops = vector_ops
        self.linking = linking_service
        self.db = db

        logger.debug("ProactiveService initialized")

    def search_with_embedding(
        self,
        embedding: np.ndarray,
        touchstone: Dict[str, Any],
        query_text: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant memories using pre-computed embedding.

        This method is used by CNS MemoryRelevanceService, which provides
        embeddings from EmbeddedMessage. Each search is fresh with no
        deduplication across messages.

        Args:
            embedding: Pre-computed 384-dimensional embedding vector
            query_text: Original query text (used for BM25 and reranking)
            touchstone: Required structured context from analysis generator
            limit: Maximum number of memories to return

        Returns:
            List of relevant memory dictionaries with metadata

        Raises:
            Exception: If search operations fail
        """
        if limit is None:
            limit = self.config.max_memories

        # Touchstone is required - extract intent for search strategy
        intent_text = touchstone['conversational_intent'].lower()
        if 'recall' in intent_text or 'remember' in intent_text:
            search_intent = 'recall'
        elif 'explore' in intent_text or 'learn' in intent_text:
            search_intent = 'explore'
        elif 'exact' in intent_text or 'specific' in intent_text:
            search_intent = 'exact'
        else:
            search_intent = 'general'

        # Enhance query with semantic hooks
        semantic_hooks = touchstone['semantic_hooks']
        enhanced_query = f"{query_text} {' '.join(semantic_hooks)}"

        logger.debug(f"Hybrid search with intent: {search_intent}, hooks: {semantic_hooks}")

        search_results = self.vector_ops.hybrid_search(
            query_text=enhanced_query,
            query_embedding=embedding,
            search_intent=search_intent,
            limit=limit * 2,  # Oversample for later filtering
            similarity_threshold=self.config.similarity_threshold,
            min_importance=self.config.min_importance_score
        )

        # Filter by minimum importance score
        filtered_results = [
            memory for memory in search_results
            if memory.importance_score >= self.config.min_importance_score
        ][:limit]

        if not filtered_results:
            logger.debug("No relevant memories found")
            return []

        # Include linked memories for context enrichment
        expanded_results = self._include_linked_memories(
            filtered_results
        )

        # Rerank and filter linked memories by type, confidence, importance
        reranked_results = self._rerank_with_links(expanded_results)

        # Apply cross-encoder reranking if reranker available
        if self.vector_ops.reranker_available:
            # Build rich context from touchstone components
            context_parts = []

            if touchstone['temporal_context']:
                context_parts.append(f"Timeline: {touchstone['temporal_context']}")
            if touchstone['relationship_context']:
                context_parts.append(f"About user: {touchstone['relationship_context']}")

            context_parts.append(f"Context: {touchstone['narrative']}")
            context_parts.append(f"Current focus: {query_text}")

            rerank_context = ' '.join(context_parts)

            final_results = self.vector_ops.rerank_memories(
                query=rerank_context,
                memories=reranked_results,
                top_k=limit
            )
            logger.info(
                f"Cross-encoder reranked {len(reranked_results)} memories to top {limit}"
            )
        else:
            final_results = reranked_results[:limit]

        logger.info(
            f"Found {len(final_results)} relevant memories after all reranking "
            f"({len(filtered_results)} primary)"
        )

        return [self._memory_to_dict(m) for m in final_results]

    def _include_linked_memories(
        self,
        primary_memories: List[Any]
    ) -> List[Any]:
        """
        Include memories linked to primary search results with hierarchical structure.

        Delegates to linking service to traverse memory graph and attach
        related memories as children of primary memories, preserving link
        metadata for display.

        Args:
            primary_memories: List of primary memory search results

        Returns:
            List of primary memories with linked_memories attribute populated

        Raises:
            Exception: If memory graph traversal fails
        """
        if not primary_memories:
            return []

        for primary_memory in primary_memories:
            # Delegate to linking service for graph traversal
            linked_with_metadata = self.linking.traverse_related(
                memory_id=primary_memory.id,
                depth=self.config.max_link_traversal_depth
            )

            # Attach linked memories with metadata to primary memory
            primary_memory.linked_memories = []

            for linked_data in linked_with_metadata:
                # Extract memory and attach metadata
                linked_memory = linked_data["memory"]
                linked_memory.link_metadata = {
                    "link_type": linked_data["link_type"],
                    "confidence": linked_data["confidence"],
                    "reasoning": linked_data["reasoning"],
                    "depth": linked_data["depth"],
                    "linked_from_id": linked_data["linked_from_id"]
                }
                primary_memory.linked_memories.append(linked_memory)

            logger.debug(
                f"Attached {len(primary_memory.linked_memories)} linked memories "
                f"to primary memory {primary_memory.id}"
            )

        return primary_memories

    def _rerank_with_links(self, primary_memories: List[Any]) -> List[Any]:
        """
        Rerank and filter memories considering link types, confidence, and importance.

        RANKING FORMULA:
        ================
        final_score = type_weight × inherited_importance × confidence

        Where:
        - type_weight: Link type priority (see LINK_TYPE_WEIGHTS below)
        - inherited_importance: (linked_importance × 0.7) + (primary_importance × 0.3)
        - confidence: Link confidence from LLM classification (0.0-1.0)

        FILTERING:
        ==========
        1. Confidence: Remove links with confidence < 0.6
        2. Deduplication: If memory appears as both primary and linked, keep in primary only
        3. After scoring: Sort linked memories by final_score (descending)

        EXAMPLE:
        ========
        Primary memory (importance: 0.8)
        └─ Linked memory (importance: 0.6, link_type: "conflicts", confidence: 0.91)

        inherited_importance = (0.6 × 0.7) + (0.8 × 0.3) = 0.42 + 0.24 = 0.66
        final_score = 1.0 × 0.66 × 0.91 = 0.60

        Args:
            primary_memories: List of primary memories with linked_memories attached

        Returns:
            Filtered and reranked primary memories with pruned linked_memories
        """
        # Link type priority weights (higher = more important)
        LINK_TYPE_WEIGHTS = {
            "conflicts": 1.0,          # Critical for accuracy
            "invalidated_by": 1.0,     # Critical for accuracy
            "supersedes": 0.9,         # High priority (versioning)
            "causes": 0.8,             # Medium-high (reasoning context)
            "motivated_by": 0.8,       # Medium-high (reasoning context)
            "instance_of": 0.7,        # Medium (supporting context)
            "shares_entity": 0.4,      # Lower (unless entity is central to query)
        }
        MIN_CONFIDENCE = 0.6

        # Collect all primary memory IDs for deduplication
        primary_ids = {str(m.id) for m in primary_memories}

        for primary_memory in primary_memories:
            if not hasattr(primary_memory, 'linked_memories'):
                continue

            linked_memories = primary_memory.linked_memories
            if not linked_memories:
                continue

            # Filter and score linked memories
            scored_linked = []

            for linked in linked_memories:
                # Deduplication: skip if this memory is already a primary
                if str(linked.id) in primary_ids:
                    logger.debug(f"Deduplicating: {linked.id} appears as both primary and linked")
                    continue

                # Get link metadata
                link_meta = getattr(linked, 'link_metadata', {})
                link_type = link_meta.get('link_type', 'unknown')
                confidence = link_meta.get('confidence')

                # Confidence filtering
                if confidence is not None and confidence < MIN_CONFIDENCE:
                    logger.debug(f"Filtering low-confidence link: {link_type} ({confidence:.2f})")
                    continue

                # Type-based weighting
                type_weight = 0.5  # Default for unknown types
                for known_type, weight in LINK_TYPE_WEIGHTS.items():
                    if link_type == known_type or link_type.startswith(f"{known_type}:"):
                        type_weight = weight
                        break

                # Importance inheritance: combine linked memory's importance with primary's
                linked_importance = getattr(linked, 'importance_score', 0.5)
                primary_importance = getattr(primary_memory, 'importance_score', 0.5)
                inherited_importance = (linked_importance * 0.7) + (primary_importance * 0.3)

                # Compute final score: type_weight * inherited_importance * confidence
                final_score = type_weight * inherited_importance * (confidence or 1.0)

                scored_linked.append((linked, final_score))

            # Sort by score (descending) and update linked_memories
            scored_linked.sort(key=lambda x: x[1], reverse=True)
            primary_memory.linked_memories = [linked for linked, score in scored_linked]

            logger.debug(
                f"Primary {primary_memory.id}: kept {len(primary_memory.linked_memories)} "
                f"linked memories after reranking (filtered {len(linked_memories) - len(primary_memory.linked_memories)})"
            )

        return primary_memories

    def _memory_to_dict(self, memory) -> Dict[str, Any]:
        """
        Convert Memory model to dictionary with hierarchical structure.

        Includes link metadata and recursively processes linked memories
        for tree-based display.

        Args:
            memory: Memory Pydantic model

        Returns:
            Dictionary representation with nested linked_memories
        """
        result = {
            "id": str(memory.id),
            "text": memory.text,
            "importance_score": memory.importance_score,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "last_accessed": memory.last_accessed.isoformat() if memory.last_accessed else None,
            "access_count": memory.access_count,
            "happens_at": memory.happens_at.isoformat() if memory.happens_at else None,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
            "inbound_links": memory.inbound_links if hasattr(memory, 'inbound_links') else [],
            "outbound_links": memory.outbound_links if hasattr(memory, 'outbound_links') else [],
        }

        # Add link metadata if this is a linked memory
        if hasattr(memory, 'link_metadata'):
            result['link_metadata'] = memory.link_metadata

        # Recursively process linked memories
        if hasattr(memory, 'linked_memories') and memory.linked_memories:
            result['linked_memories'] = [
                self._memory_to_dict(linked)
                for linked in memory.linked_memories
            ]
        else:
            result['linked_memories'] = []

        return result
