"""
Vector operations for LT_Memory system.

Handles embedding generation and storage using AllMiniLM (384d) embeddings.
Singleton service that wraps the embeddings provider and database access.
"""
import logging
import numpy as np
from typing import List, Optional, Union, Dict
from uuid import UUID

from lt_memory.models import Memory, ExtractedMemory
from lt_memory.db_access import LTMemoryDB
from lt_memory.hybrid_search import HybridSearcher

logger = logging.getLogger(__name__)


class VectorOps:
    """
    Vector operations service for embedding generation and similarity search.

    Provides embedding operations with AllMiniLM (384d).
    """

    def __init__(self, embeddings_provider, db: LTMemoryDB):
        """
        Initialize vector operations service.

        Args:
            embeddings_provider: Hybrid embeddings provider singleton
            db: LTMemoryDB instance for database access
        """
        self.embeddings_provider = embeddings_provider
        self.db = db

        # Check if reranker is available
        self.reranker_available = (
            hasattr(embeddings_provider, 'enable_reranker') and
            embeddings_provider.enable_reranker and
            hasattr(embeddings_provider, 'rerank')
        )

        # Initialize hybrid searcher
        self.hybrid_searcher = HybridSearcher(db)

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate AllMiniLM embedding (384d) for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        embedding = self.embeddings_provider.encode_realtime([text])[0]

        if isinstance(embedding, np.ndarray):
            return embedding.tolist()
        return embedding

    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate AllMiniLM embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        embeddings = self.embeddings_provider.encode_realtime(texts)

        result = []
        for embedding in embeddings:
            if isinstance(embedding, np.ndarray):
                result.append(embedding.tolist())
            else:
                result.append(embedding)

        return result

    def store_memories_with_embeddings(
        self,
        memories: List[ExtractedMemory]
    ) -> List[UUID]:
        """
        Generate embeddings and store multiple memories.

        Args:
            memories: List of ExtractedMemory objects

        Returns:
            List of created memory UUIDs
        """
        if not memories:
            return []

        texts = [memory.text for memory in memories]
        embeddings = self.generate_embeddings_batch(texts)

        return self.db.store_memories(
            memories=memories,
            embeddings=embeddings
        )

    def _search_with_embedding(
        self,
        query_embedding: List[float],
        limit: int,
        similarity_threshold: float,
        min_importance: float
    ) -> List[Memory]:
        """
        Internal method that performs vector similarity search.

        Args:
            query_embedding: Embedding vector (384d AllMiniLM)
            limit: Maximum results to return
            similarity_threshold: Minimum cosine similarity (0-1)
            min_importance: Minimum importance score filter

        Returns:
            List of Memory models sorted by similarity
        """
        return self.db.search_similar(
            query_embedding=query_embedding,
            limit=limit,
            similarity_threshold=similarity_threshold,
            min_importance=min_importance
        )

    def find_similar_memories(
        self,
        query: str,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        min_importance: float = 0.1
    ) -> List[Memory]:
        """
        Find similar memories using vector similarity search from text query.

        Use this when you have a text query and need to find relevant memories.
        For example: "Find memories about Python" or user's current message.

        Args:
            query: Query text to search for
            limit: Maximum results to return
            similarity_threshold: Minimum cosine similarity (0-1)
            min_importance: Minimum importance score filter

        Returns:
            List of Memory models sorted by similarity

        See also:
            find_similar_by_embedding: Use when you already have an embedding
            find_similar_to_memory: Use when expanding from an existing memory
        """
        query_embedding = self.generate_embedding(query)

        return self._search_with_embedding(
            query_embedding=query_embedding,
            limit=limit,
            similarity_threshold=similarity_threshold,
            min_importance=min_importance
        )

    def find_similar_by_embedding(
        self,
        query_embedding: Union[List[float], np.ndarray],
        query_text: Optional[str] = None,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        min_importance: float = 0.1
    ) -> List[Memory]:
        """
        Find similar memories using pre-computed embedding.

        Use when embedding already exists (e.g., from CNS EmbeddedMessage)
        to avoid redundant embedding generation. For new text queries, use
        find_similar_memories() instead.

        Args:
            query_embedding: Pre-computed embedding vector (384d AllMiniLM)
            query_text: Optional original text (used for reranking if available)
            limit: Maximum results to return
            similarity_threshold: Minimum cosine similarity (0-1)
            min_importance: Minimum importance score filter

        Returns:
            List of Memory models sorted by similarity

        See also:
            find_similar_memories: Use when starting from text query
            find_similar_to_memory: Use when expanding from existing memory
        """
        # Convert numpy array to list if needed
        if isinstance(query_embedding, np.ndarray):
            query_embedding = query_embedding.tolist()

        # Validate dimensions
        if len(query_embedding) != 384:
            raise ValueError(
                f"Expected 384-dimensional embedding (AllMiniLM), got {len(query_embedding)}"
            )

        results = self._search_with_embedding(
            query_embedding=query_embedding,
            limit=limit,
            similarity_threshold=similarity_threshold,
            min_importance=min_importance
        )

        # Apply reranking if text provided and reranker available
        if query_text and results and self.reranker_available:
            results = self.rerank_memories(query_text, results, top_k=limit)

        return results

    def find_similar_to_memory(
        self,
        memory_id: UUID,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        min_importance: float = 0.1
    ) -> List[Memory]:
        """
        Find memories similar to an existing memory using its embedding.

        Use this when expanding from a known memory - for relationship discovery,
        cluster formation, or finding related context. More efficient than
        find_similar_memories since the embedding already exists.

        Args:
            memory_id: Reference memory UUID to find neighbors for
            limit: Maximum results to return
            similarity_threshold: Minimum cosine similarity (0-1)
            min_importance: Minimum importance score filter

        Returns:
            List of Memory models sorted by similarity (excludes reference memory)

        See also:
            find_similar_memories: Use when starting from a text query
        """
        reference_memory = self.db.get_memory(memory_id)

        if not reference_memory or not reference_memory.embedding:
            logger.warning(f"Memory {memory_id} not found or has no embedding")
            return []

        results = self.db.search_similar(
            query_embedding=reference_memory.embedding,
            limit=limit + 1,
            similarity_threshold=similarity_threshold,
            min_importance=min_importance
        )

        return [m for m in results if m.id != memory_id][:limit]

    def update_memory_embedding(
        self,
        memory_id: UUID,
        new_text: str
    ) -> Memory:
        """
        Update memory text and regenerate embedding.

        Args:
            memory_id: Memory UUID
            new_text: New text content

        Returns:
            Updated Memory model

        Raises:
            ValueError: If memory not found
        """
        new_embedding = self.generate_embedding(new_text)

        updated_memory = self.db.update_memory(
            memory_id,
            updates={
                'text': new_text,
                'embedding': new_embedding
            }
        )

        logger.info(f"Updated memory {memory_id} with new text and embedding")
        return updated_memory

    def rerank_memories(
        self,
        query: str,
        memories: List[Memory],
        top_k: int = 10
    ) -> List[Memory]:
        """
        Rerank memories using reranker model if available.

        Falls back to original order if reranker not available.

        Args:
            query: Query text
            memories: List of memories to rerank
            top_k: Number of top results to return

        Returns:
            Reranked list of memories
        """
        if not self.reranker_available or not memories:
            return memories[:top_k]

        try:
            texts = [m.text for m in memories]

            reranked_results = self.embeddings_provider.rerank(
                query=query,
                passages=texts,
                top_k=top_k
            )

            # Extract indices from (index, score, passage) tuples
            reranked_memories = [memories[idx] for idx, score, passage in reranked_results]

            logger.debug(f"Reranked {len(memories)} memories to top {top_k}")
            return reranked_memories

        except Exception as e:
            logger.warning(f"Reranking failed, returning original order: {e}")
            return memories[:top_k]

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: Union[List[float], np.ndarray],
        search_intent: str = "general",
        limit: int = 10,
        similarity_threshold: float = 0.7,
        min_importance: float = 0.1
    ) -> List[Memory]:
        """
        Perform hybrid BM25 + vector search for optimal memory retrieval.

        Combines exact phrase matching with semantic similarity, weighted
        based on search intent for different use cases.

        Args:
            query_text: Text query for BM25 search
            query_embedding: Pre-computed embedding for vector search
            search_intent: Intent type from touchstone analysis
            limit: Maximum results to return
            similarity_threshold: Minimum similarity for vector search
            min_importance: Minimum importance score

        Returns:
            List of Memory models ranked by hybrid score
        """
        # Convert numpy array to list if needed
        if isinstance(query_embedding, np.ndarray):
            query_embedding = query_embedding.tolist()

        return self.hybrid_searcher.hybrid_search(
            query_text=query_text,
            query_embedding=query_embedding,
            search_intent=search_intent,
            limit=limit,
            similarity_threshold=similarity_threshold,
            min_importance=min_importance
        )

    def cleanup(self):
        """
        Clean up resources.

        No-op: Dependencies managed by factory lifecycle.
        Nulling references breaks in-flight scheduler jobs.
        """
        logger.debug("VectorOps cleanup completed (no-op)")
