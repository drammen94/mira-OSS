"""
Temporal context service for continuum augmentation.

Handles retrieval of historical continuum context from explicitly linked days,
using LlamaIndex for sophisticated semantic search across continuum history.

NOTE: This service now accepts a shared EmbeddingsProvider to avoid duplicate BGE model loading.
GLOBAL FIX NEEDED: Apply this pattern system-wide - any component that uses embeddings should
receive the EmbeddingsProvider via dependency injection rather than creating new instances.
Components to audit: MemoryExtractionService, VectorStore, ClassificationEngine, etc.
"""
import logging
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
import numpy as np

from lt_memory.db_access import LTMemoryDB
from utils.database_session_manager import get_shared_session_manager
from clients.hybrid_embeddings_provider import get_hybrid_embeddings_provider
from cns.core.embedded_message import EmbeddedMessage

logger = logging.getLogger(__name__)

# Global temporal indices cache (shared across all service instances)
_global_temporal_indices = {}

# Module-level singleton instance
_temporal_service_instance = None


def get_temporal_context_service(embeddings_provider=None) -> 'TemporalContextService':
    """
    Get or create singleton TemporalContextService instance.
    
    Following the pattern from get_valkey_client() and get_bge_embedding_model(),
    this ensures we reuse the same temporal service and avoid expensive
    reinitialization of embeddings and LlamaIndex settings.
    
    Args:
        embeddings_provider: Optional EmbeddingsProvider to use. If None, uses singleton.
    
    Returns:
        Singleton TemporalContextService instance
    """
    global _temporal_service_instance
    if _temporal_service_instance is None:
        from config.config_manager import config
        provider = embeddings_provider or get_hybrid_embeddings_provider()
        logger.info("Creating singleton TemporalContextService instance")
        _temporal_service_instance = TemporalContextService(config, provider)
    return _temporal_service_instance

# Conditional LlamaIndex imports
try:
    from llama_index.core import VectorStoreIndex, Document, Settings
    from llama_index.core.embeddings import BaseEmbedding
    from llama_index.core.llms import MockLLM
    from llama_index.core.node_parser import SentenceSplitter
    LLAMA_INDEX_AVAILABLE = True
except ImportError:
    LLAMA_INDEX_AVAILABLE = False
    logger.warning("LlamaIndex not available. Temporal RAG features will be disabled.")


class MIRAEmbeddingAdapter(BaseEmbedding if LLAMA_INDEX_AVAILABLE else object):
    """Adapter to use MIRA's EmbeddingsProvider with LlamaIndex."""
    
    def __init__(self, embeddings_provider):
        """Initialize with MIRA's embeddings provider."""
        if LLAMA_INDEX_AVAILABLE:
            super().__init__(embed_batch_size=10)  # LlamaIndex requires this parameter
        self._embeddings_provider = embeddings_provider
        
    @property
    def embedding_dim(self) -> int:
        """Return embedding dimension (1024 for MIRA)."""
        return 1024
    
    def _get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query string."""
        # Use deep model for temporal RAG (BGE-M3)
        embedding = self._embeddings_provider.encode_deep(query)
        return embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
    
    def _get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for a text string."""
        # Use deep model for temporal RAG (BGE-M3)
        embedding = self._embeddings_provider.encode_deep(text)
        return embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
    
    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts."""
        # Use deep model for temporal RAG (BGE-M3)
        embeddings = self._embeddings_provider.encode_deep(texts)
        # Handle both single and batch results
        if len(embeddings.shape) == 1:
            # Single embedding case
            return [embeddings.tolist()]
        else:
            # Batch embeddings case
            return [emb.tolist() if isinstance(emb, np.ndarray) else emb for emb in embeddings]
    
    def _aget_query_embedding(self, query: str) -> List[float]:
        """Async version - just calls sync for now."""
        return self._get_query_embedding(query)
    
    def _aget_text_embedding(self, text: str) -> List[float]:
        """Async version - just calls sync for now."""
        return self._get_text_embedding(text)
        
    def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Async version - just calls sync for now."""
        return self._get_text_embeddings(texts)


class TemporalContextService:
    """
    Service for retrieving temporal context from linked continuum days.
    
    Uses LlamaIndex to create sophisticated semantic indices of historical
    conversations, enabling rich contextual retrieval for current continuums.
    """
    
    def __init__(self, config, embeddings_provider=None):
        """
        Initialize temporal context service.
        
        Args:
            config: Application configuration
            embeddings_provider: Optional shared EmbeddingsProvider instance
        """
        self.config = config
        self.enabled = config.lt_memory.temporal_rag_enabled and LLAMA_INDEX_AVAILABLE
        
        # Initialize database connection
        if self.enabled:
            session_manager = get_shared_session_manager()
            self.db = LTMemoryDB(session_manager)
            self.embeddings_provider = embeddings_provider or get_hybrid_embeddings_provider()  # Use singleton
            
            # Reference to global temporal indices cache
            self.temporal_indices = _global_temporal_indices
            
            # Initialize LlamaIndex settings
            self._initialize_settings()
        else:
            self.db = None
            self.embeddings_provider = None
            
        logger.info(f"TemporalContextService initialized (enabled: {self.enabled})")
    
    def _initialize_settings(self):
        """Initialize LlamaIndex global settings with MIRA's embeddings."""
        if not LLAMA_INDEX_AVAILABLE:
            return
            
        try:
            # Create embedding adapter
            embed_model = MIRAEmbeddingAdapter(self.embeddings_provider)
            
            # Use MockLLM since we only need embeddings for retrieval
            llm = MockLLM()
            
            # Create text splitter with config settings
            text_splitter = SentenceSplitter(
                chunk_size=self.config.lt_memory.temporal_chunk_size,
                chunk_overlap=self.config.lt_memory.temporal_chunk_overlap
            )
            
            # Configure global settings
            Settings.embed_model = embed_model
            Settings.llm = llm
            Settings.transformations = [text_splitter]
            Settings.chunk_size = self.config.lt_memory.temporal_chunk_size
            
            logger.info("LlamaIndex settings initialized for temporal RAG")
            
        except Exception as e:
            logger.error(f"Failed to initialize LlamaIndex settings: {e}")
            self.enabled = False
    
    def get_temporal_context(self, continuum, query: Optional[str] = None) -> str:
        """
        Get formatted temporal context for the current continuum.
        
        Args:
            continuum: Current continuum object
            query: Optional query text (defaults to recent continuum context)
            
        Returns:
            Formatted temporal context string to inject into system prompt
        """
        if not self.enabled:
            return ""
            
        # Check for linked days in continuum metadata
        linked_days = self._get_linked_days(continuum)
        if not linked_days:
            return ""
            
        try:
            # Build or use provided query
            if not query:
                query = self._build_context_query(continuum)
                if not query:
                    return ""
            
            # Query temporal indices for all linked days
            # LlamaIndex handles embedding generation internally
            results = self._query_temporal_indices(query, linked_days)
            if not results:
                return ""
                
            # Format results for system prompt injection
            return self._format_temporal_context(results)
            
        except Exception as e:
            logger.error(f"Error retrieving temporal context: {e}")
            return ""
    
    def _get_linked_days(self, continuum) -> List[str]:
        """Extract linked day archive IDs from continuum metadata."""
        if not hasattr(continuum, '_state') or not continuum._state.metadata:
            return []
            
        linked_days = continuum._state.metadata.get('linked_days', [])
        if not isinstance(linked_days, list):
            return []
            
        # Limit to configured maximum
        max_days = self.config.lt_memory.temporal_max_days_linked
        return linked_days[:max_days]
    
    def _build_context_query(self, continuum) -> Optional[str]:
        """Build query from recent continuum messages."""
        if not continuum.messages:
            return None
            
        # Extract recent user messages (similar to ProactiveMemoryManager)
        user_messages = []
        for message in reversed(continuum.messages):
            if message.role == "user":
                if isinstance(message.content, str):
                    user_messages.append(message.content)
                elif isinstance(message.content, list):
                    # Handle structured content
                    text_parts = [
                        block.get("text", "") for block in message.content 
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    if text_parts:
                        user_messages.append(" ".join(text_parts))
            
            # Get last 3 user messages
            if len(user_messages) >= 3:
                break
        
        if not user_messages:
            return None
            
        # Weight recent messages more heavily
        weighted_parts = []
        weights = [3, 2, 1]  # Most recent gets highest weight
        
        for i, message in enumerate(user_messages):
            if i < len(weights):
                # Add the message multiple times based on weight
                for _ in range(weights[i]):
                    weighted_parts.append(message)
        
        return " ".join(weighted_parts)
    
    def _query_temporal_indices(self, query: str, linked_days: List[str]) -> List[Dict[str, Any]]:
        """
        Query temporal indices for relevant content using LlamaIndex.
        
        Args:
            query: Query string (weighted context)
            linked_days: List of archive IDs to query
            
        Returns:
            List of temporal context results with scores
        """
        all_results = []
        
        for archive_id in linked_days:
            # Skip if index already exists and cached
            if archive_id not in self.temporal_indices:
                # We can't recreate the index here without the original date
                # This should only happen if indices were cleared but linked_days wasn't
                logger.warning(f"Temporal index {archive_id} not found in cache")
                continue
                    
            # Query the index
            index = self.temporal_indices.get(archive_id)
            if not index:
                continue
                
            try:
                # Create query engine with similarity search
                query_engine = index.as_query_engine(
                    similarity_top_k=5  # Get top 5 chunks per archive
                )
                
                response = query_engine.query(query)
                
                # Extract source nodes with metadata
                for node in response.source_nodes:
                    result = {
                        'text': node.text,
                        'score': node.score if hasattr(node, 'score') else 0.0,
                        'archive_id': archive_id,
                        'metadata': node.metadata
                    }
                    all_results.append(result)
                    
            except Exception as e:
                logger.error(f"Error querying temporal index for {archive_id}: {e}")
                
        # Sort by score and limit results
        all_results.sort(key=lambda x: x['score'], reverse=True)
        return all_results[:10]  # Top 10 results across all linked days
    
    def _create_temporal_index_from_date(self, archive_id: str, date_str: str, user_id: str) -> bool:
        """
        Create a LlamaIndex VectorStoreIndex from messages on a specific date.
        
        Args:
            archive_id: Generated ID for this temporal index
            date_str: Date string (YYYY-MM-DD) to index
            user_id: User ID for message querying
            
        Returns:
            True if index created successfully
        """
        if not LLAMA_INDEX_AVAILABLE:
            return False
            
        try:
            # Load messages for the date
            conversation_data = self._load_messages_for_date(date_str, user_id)
            if not conversation_data:
                logger.warning(f"No continuum data found for date {date_str}")
                return False
                
            # Create LlamaIndex documents from continuum
            documents = self._create_documents_from_messages(conversation_data, archive_id)
            if not documents:
                logger.warning(f"No documents created for date {date_str}")
                return False
                
            # Create vector index
            index = VectorStoreIndex.from_documents(
                documents,
                show_progress=False
            )
            
            # Cache the index
            self.temporal_indices[archive_id] = index
            logger.info(f"Created temporal index for date {date_str} with {len(documents)} documents")
            return True
            
        except Exception as e:
            logger.error(f"Error creating temporal index for date {date_str}: {e}")
            return False
    
    def _load_messages_for_date(self, date_str: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Load messages for a specific date using ContinuumRepository."""
        from datetime import datetime
        
        try:
            # Parse date and create date range for SQL query
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_datetime = datetime.combine(target_date, datetime.min.time())
            end_datetime = datetime.combine(target_date, datetime.max.time())
            
            # Use ContinuumRepository to get continuum messages for the date
            from cns.infrastructure.continuum_repository import get_continuum_repository
            repo = get_continuum_repository()
            
            # Get continuum history for the specific date range
            history_result = repo.get_history(
                user_id=user_id,
                offset=0,
                limit=1000,  # Large limit to get all messages for the day
                start_date=start_datetime,
                end_date=end_datetime
            )
            
            messages = history_result.get('messages', [])
            
            if not messages:
                logger.info(f"No messages found for date {date_str}")
                return None
                
            return {
                'messages': messages,
                'date': date_str,
                'message_count': len(messages)
            }
            
        except Exception as e:
            logger.error(f"Error loading messages for date {date_str}: {e}")
            return None
    
    def _create_documents_from_messages(self, conversation_data: Dict[str, Any], archive_id: str) -> List[Any]:
        """Create LlamaIndex documents from message data."""
        documents = []
        messages = conversation_data.get('messages', [])
        date_str = conversation_data.get('date', '')
        
        # Group messages into conversational chunks
        conversation_chunks = []
        current_chunk = []
        
        for msg in messages:
            current_chunk.append(msg)
            
            # Create chunk when we have a user-assistant exchange
            if len(current_chunk) >= 2 and current_chunk[-1].get('role') == 'assistant':
                conversation_chunks.append(current_chunk[:])
                current_chunk = []
        
        # Add any remaining messages
        if current_chunk:
            conversation_chunks.append(current_chunk)
        
        # Create documents from chunks
        for i, chunk in enumerate(conversation_chunks):
            # Format chunk as natural continuum
            text_parts = []
            for msg in chunk:
                role = "Human" if msg.get('role') == 'user' else "Assistant"
                content = msg.get('content', '')
                # Handle structured content if needed
                if isinstance(content, list):
                    content = ' '.join([block.get('text', '') for block in content if isinstance(block, dict)])
                text_parts.append(f"{role}: {content}")
            
            chunk_text = "\n\n".join(text_parts)
            
            # Create document with metadata
            metadata = {
                'chunk_index': i,
                'archive_id': archive_id,
                'conversation_date': date_str,
                'message_count': len(chunk),
                'chunk_type': 'continuum'
            }
            
            if not LLAMA_INDEX_AVAILABLE:
                return []
            
            doc = Document(
                text=chunk_text,
                metadata=metadata
            )
            documents.append(doc)
        
        return documents
    
    def _format_temporal_context(self, results: List[Dict[str, Any]]) -> str:
        """Format temporal query results for system prompt injection."""
        if not results:
            return ""
            
        context_parts = [
            "# Linked Historical Context",
            "The following relevant continuum excerpts are from days the user has explicitly linked:",
            ""
        ]
        
        # Group by archive/date for organization
        by_archive = {}
        for result in results:
            archive_id = result['archive_id']
            if archive_id not in by_archive:
                by_archive[archive_id] = {
                    'date': result['metadata'].get('conversation_date', 'Unknown date'),
                    'results': []
                }
            by_archive[archive_id]['results'].append(result)
        
        # Format each archive's results
        for archive_id, archive_data in by_archive.items():
            context_parts.append(f"## From {archive_data['date']}:")
            
            # Add top results from this day
            for result in archive_data['results'][:3]:  # Limit per day
                # Add relevance indicator
                relevance = "HIGH" if result['score'] > 0.8 else "MEDIUM" if result['score'] > 0.6 else "RELEVANT"
                context_parts.append(f"[{relevance}] {result['text'][:500]}...")
                context_parts.append("")
        
        return "\n".join(context_parts)
    
    def link_day(self, continuum, date_str: str, user_id: str) -> Optional[str]:
        """
        Link a continuum day to the current continuum by generating an archive.
        
        Args:
            continuum: Current continuum object
            date_str: Date string (YYYY-MM-DD) to link
            user_id: User ID for message querying
            
        Returns:
            Generated archive_id if successfully linked, None otherwise
        """
        if not self.enabled:
            return None
            
        try:
            # Get current linked days
            linked_days = self._get_linked_days(continuum)
            
            # Check limit
            max_days = self.config.lt_memory.temporal_max_days_linked
            if len(linked_days) >= max_days:
                logger.warning(f"Cannot link more than {max_days} days")
                return None
                
            # Generate archive ID for this date
            import uuid
            archive_id = str(uuid.uuid4())
            
            # Create temporal index for this date
            success = self._create_temporal_index_from_date(archive_id, date_str, user_id)
            if not success:
                return None
                
            # Add to linked days
            linked_days.append(archive_id)
            
            # Update continuum metadata
            if not hasattr(continuum._state, 'metadata'):
                continuum._state.metadata = {}
            continuum._state.metadata['linked_days'] = linked_days
            
            logger.info(f"Linked date {date_str} as archive {archive_id} to continuum")
            return archive_id
            
        except Exception as e:
            logger.error(f"Error linking day: {e}")
            return None
    
    def unlink_day(self, continuum, archive_id: str) -> bool:
        """
        Unlink a continuum day from the current continuum.
        
        Args:
            continuum: Current continuum object
            archive_id: Archive ID to unlink
            
        Returns:
            True if successfully unlinked
        """
        if not self.enabled:
            return False
            
        try:
            # Get current linked days
            linked_days = self._get_linked_days(continuum)
            
            # Remove if present
            if archive_id in linked_days:
                linked_days.remove(archive_id)
                continuum._state.metadata['linked_days'] = linked_days
                
                # Remove cached index
                if archive_id in self.temporal_indices:
                    del self.temporal_indices[archive_id]
                
                logger.info(f"Unlinked archive {archive_id} from continuum")
                return True
            else:
                logger.info(f"Archive {archive_id} was not linked")
                return False
                
        except Exception as e:
            logger.error(f"Error unlinking day: {e}")
            return False
    
    def clear_linked_days(self, continuum) -> None:
        """Clear all linked days from continuum (used during daily cleanup)."""
        if hasattr(continuum, '_state') and continuum._state.metadata:
            # Clear from metadata
            linked_days = continuum._state.metadata.pop('linked_days', [])
            
            # Clear cached indices
            for archive_id in linked_days:
                if archive_id in self.temporal_indices:
                    del self.temporal_indices[archive_id]
                    
            logger.info(f"Cleared {len(linked_days)} linked days from continuum")
    
    def cleanup(self):
        """Clean up resources."""
        # Clear all cached indices (affects global cache)
        self.temporal_indices.clear()
        logger.info("TemporalContextService cleanup completed")