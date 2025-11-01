"""
Hybrid embeddings provider that manages both fast and deep models.

This provider orchestrates AllMiniLM for real-time operations and OpenAI
for advanced features, optimizing for both speed and quality.
"""
import logging
import hashlib
from typing import List, Union, Optional, Tuple
import numpy as np

from clients.embeddings.sentence_transformers import get_all_minilm_model

logger = logging.getLogger(__name__)

# Module-level singleton instance
_hybrid_provider_instance = None


class EmbeddingCache:
    """
    Valkey-backed embedding cache with 15-minute TTL.

    Raises if Valkey is unreachable - embedding cache requires Valkey.
    """

    def __init__(self, key_prefix: str = "embedding"):
        self.logger = logging.getLogger("embedding_cache")
        self.key_prefix = key_prefix
        from clients.valkey_client import get_valkey_client
        self.valkey = get_valkey_client()  # Raises if Valkey unreachable
        self.logger.info(f"Embedding cache initialized with Valkey backend (prefix: {key_prefix})")
    
    def _get_cache_key(self, text: str) -> str:
        return f"{self.key_prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    
    def get(self, text: str) -> Optional[np.ndarray]:
        """
        Get cached embedding.

        Returns None if key not found (cache miss).
        Raises if Valkey operation fails.
        """
        cache_key = self._get_cache_key(text)
        cached_data = self.valkey.valkey_binary.get(cache_key)
        if cached_data:
            # Cache stores fp16 data
            return np.frombuffer(cached_data, dtype=np.float16)

        return None  # Cache miss (key not found)
    
    def set(self, text: str, embedding: np.ndarray) -> None:
        """
        Cache embedding with 15-minute TTL.

        Raises if Valkey operation fails.
        """
        cache_key = self._get_cache_key(text)
        # Store as fp16 to save cache memory
        embedding_bytes = embedding.astype(np.float16).tobytes()
        self.valkey.valkey_binary.setex(cache_key, 900, embedding_bytes)  # 15 minutes


def get_hybrid_embeddings_provider(cache_enabled: bool = True, enable_reranker: bool = True) -> 'HybridEmbeddingsProvider':
    """
    Get or create singleton HybridEmbeddingsProvider instance.
    
    Args:
        cache_enabled: Whether to enable embedding caching
        enable_reranker: Whether to enable BGE reranker for search refinement
    
    Returns:
        Singleton HybridEmbeddingsProvider instance
    """
    global _hybrid_provider_instance
    if _hybrid_provider_instance is None:
        logger.info("Creating singleton HybridEmbeddingsProvider instance")
        _hybrid_provider_instance = HybridEmbeddingsProvider(
            cache_enabled=cache_enabled,
            enable_reranker=enable_reranker
        )
    return _hybrid_provider_instance


class HybridEmbeddingsProvider:
    """
    Manages both fast (AllMiniLM) and deep (OpenAI) embedding models.
    
    Architecture:
    - AllMiniLM (384-dim): Real-time operations (~1ms)
    - OpenAI text-embedding-3-small (1024-dim): Advanced features
    
    Each model has its own cache to prevent dimension conflicts.
    """
    
    def __init__(self, cache_enabled: bool = True, enable_reranker: bool = True):
        """
        Initialize the hybrid provider with both models.
        
        Args:
            cache_enabled: Whether to enable embedding caching
            enable_reranker: Whether to enable BGE reranker for search refinement
        """
        self.logger = logging.getLogger("hybrid_embeddings")
        self.cache_enabled = cache_enabled
        self.enable_reranker = enable_reranker
        
        # Load both models immediately
        from config.config_manager import config
        
        self.logger.info("Loading AllMiniLM model for real-time operations")
        self.fast_model = get_all_minilm_model(
            model_name=config.embeddings.fast_model.model_name,
            cache_dir=config.embeddings.fast_model.cache_dir,
            thread_limit=config.embeddings.fast_model.thread_limit
        )
        
        self.logger.info("Loading OpenAI embeddings for deep understanding")
        from clients.embeddings.openai_embeddings import OpenAIEmbeddingModel
        # Use text-embedding-3-small which has 1024 dimensions, same as BGE-M3
        self.deep_model = OpenAIEmbeddingModel(model="text-embedding-3-small")
        
        # Initialize separate caches for each model with different prefixes
        if cache_enabled:
            self.fast_cache = EmbeddingCache(key_prefix="embedding_384")  # 384-dim cache
            self.deep_cache = EmbeddingCache(key_prefix="embedding_1024")  # 1024-dim cache
        else:
            self.fast_cache = None
            self.deep_cache = None
        
        # Initialize reranker if enabled (BGE reranker works independently of embedding model)
        if enable_reranker:
            self.logger.info("Loading BGE reranker for search refinement")
            from clients.embeddings.bge_reranker import get_bge_reranker
            self._reranker = get_bge_reranker(
                model_name="BAAI/bge-reranker-base",
                use_fp16=True,  # Default to FP16 for efficiency
                cache_dir=config.embeddings.deep_model.cache_dir,
                thread_limit=config.embeddings.deep_model.thread_limit
            )
        else:
            self._reranker = None
        
        self.logger.info(f"HybridEmbeddingsProvider initialized with OpenAI deep model (reranker: {enable_reranker})")
    
    def encode_realtime(self,
                       texts: Union[str, List[str]],
                       batch_size: Optional[int] = None) -> np.ndarray:
        """
        Generate fast 384-dimensional embeddings for real-time operations.
        
        Used for:
        - Tool relevance classification
        - Memory search and proactive surfacing
        - Workflow detection
        - Memory storage
        
        Args:
            texts: Text or list of texts to encode
            batch_size: Batch size for encoding
            
        Returns:
            384-dimensional normalized embeddings
        """
        if batch_size is None:
            from config.config_manager import config
            batch_size = config.embeddings.fast_model.batch_size
        
        # Handle caching for single text
        if self.cache_enabled and isinstance(texts, str) and self.fast_cache:
            cached = self.fast_cache.get(texts)
            if cached is not None:
                return cached
        
        # Generate embeddings
        embeddings = self.fast_model.encode(texts, batch_size=batch_size)
        
        # Convert to fp16 for memory efficiency
        embeddings = embeddings.astype(np.float16)
        
        # Cache single embeddings
        if self.cache_enabled and isinstance(texts, str) and self.fast_cache:
            self.fast_cache.set(texts, embeddings)
        
        return embeddings
    
    def encode_deep(self,
                   texts: Union[str, List[str]],
                   batch_size: Optional[int] = None) -> np.ndarray:
        """
        Generate deep 1024-dimensional embeddings for advanced features.
        
        Used for:
        - Temporal RAG retrieval
        - Conversational search
        - Long-form content understanding
        
        Args:
            texts: Text or list of texts to encode
            batch_size: Batch size for encoding
            
        Returns:
            1024-dimensional normalized embeddings
        """
        if batch_size is None:
            from config.config_manager import config
            batch_size = config.embeddings.deep_model.batch_size
        
        # Handle caching for single text
        if self.cache_enabled and isinstance(texts, str) and self.deep_cache:
            cached = self.deep_cache.get(texts)
            if cached is not None:
                return cached
        
        # Generate embeddings
        embeddings = self.deep_model.encode(texts, batch_size=batch_size)
        
        # Convert to fp16 for memory efficiency
        embeddings = embeddings.astype(np.float16)
        
        # Cache single embeddings
        if self.cache_enabled and isinstance(texts, str) and self.deep_cache:
            self.deep_cache.set(texts, embeddings)
        
        return embeddings
    
    def rerank(self,
               query: str,
               passages: List[str],
               top_k: int = 10) -> List[tuple]:
        """
        Rerank passages using BGE reranker for improved relevance scoring.
        
        Args:
            query: Search query text
            passages: List of passages to rerank
            top_k: Number of top results to return
            
        Returns:
            List of tuples (original_index, relevance_score, passage_text) 
            sorted by relevance score in descending order
            
        Raises:
            RuntimeError: If reranker is not enabled or available
        """
        if not self.enable_reranker or not self._reranker:
            raise RuntimeError(
                "Reranker not available. Initialize HybridEmbeddingsProvider "
                "with enable_reranker=True to use reranking functionality."
            )
        
        if not passages:
            return []
        
        if query is None:
            raise ValueError("Query cannot be None for reranking")
        
        try:
            # Get reranked results with scores
            scores_with_indices = self._reranker.rerank(
                query, passages, return_scores=True
            )
            
            # Take top_k results
            top_results = scores_with_indices[:top_k]
            
            # Format as (index, score, passage) tuples
            # Ensure native Python types (not numpy)
            results = [
                (int(idx), float(score), passages[idx])
                for idx, score in top_results
            ]
            
            return results
            
        except Exception as e:
            self.logger.error(f"Reranking failed: {e}")
            raise
    
    def search_and_rerank(self,
                          query: str,
                          passages: List[str],
                          passage_embeddings: Optional[np.ndarray] = None,
                          embedding_model: str = "fast",
                          initial_top_k: int = 50,
                          final_top_k: int = 10) -> List[Tuple[int, float, str]]:
        """
        Two-stage retrieval: fast embedding similarity search followed by neural reranking.
        
        Args:
            query: Search query text
            passages: List of passages to search through
            passage_embeddings: Pre-computed embeddings (optional). If None, will compute them.
            embedding_model: Which embedding model to use ("fast" for realtime, "deep" for quality)
            initial_top_k: Number of candidates to retrieve in first stage
            final_top_k: Number of results to return after reranking
            
        Returns:
            List of tuples (original_index, rerank_score, passage_text) 
            sorted by rerank score in descending order
        """
        try:
            # Stage 1: Fast embedding-based similarity search
            if passage_embeddings is None:
                if embedding_model == "fast":
                    passage_embeddings = self.encode_realtime(passages)
                else:
                    passage_embeddings = self.encode_deep(passages)
            
            if embedding_model == "fast":
                query_embedding = self.encode_realtime(query)
            else:
                query_embedding = self.encode_deep(query)
            
            # Compute cosine similarities
            similarities = np.dot(passage_embeddings, query_embedding)
            
            # Get top candidates from embedding similarity
            top_indices = np.argsort(similarities)[::-1][:initial_top_k]
            top_passages = [passages[i] for i in top_indices]
            
            # Stage 2: Neural reranking (if available)
            if self.enable_reranker and self._reranker:
                reranked_results = self.rerank(query, top_passages, top_k=final_top_k)
                # Convert local indices back to original indices
                final_results = [
                    (int(top_indices[local_idx]), score, passage)
                    for local_idx, score, passage in reranked_results
                ]
                return final_results
            else:
                # Fallback: return top results based on embedding similarity
                results = [
                    (int(idx), float(similarities[idx]), passages[idx])
                    for idx in top_indices[:final_top_k]
                ]
                return results
                
        except Exception as e:
            self.logger.error(f"Search and rerank failed: {e}")
            raise
    
    def close(self):
        """Clean up resources for both models and reranker."""
        self.fast_model.close()
        # OpenAI model has close method too
        if hasattr(self.deep_model, 'close'):
            self.deep_model.close()
        if self._reranker:
            self._reranker.close()
        self.logger.info("HybridEmbeddingsProvider closed")