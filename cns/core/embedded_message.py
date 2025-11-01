"""
Embedded message structure for propagating pre-computed embeddings.

This structure carries user messages with their embeddings through the system,
eliminating duplicate encoding operations across services.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class EmbeddedMessage:
    """
    Message with pre-computed embeddings for efficient propagation.
    
    This structure is created at the CNS entry point, used by all services,
    and destroyed after request completion to prevent memory retention.
    
    Attributes:
        content: The original message text
        weighted_context: The weighted context built from recent messages
        user_id: ID of the user who sent the message
        embedding_384: 384-dimensional embedding of weighted_context (AllMiniLM)
        metadata: Additional message metadata
    """
    content: str  # Original user message
    weighted_context: str  # Weighted context for embedding
    user_id: str
    embedding_384: np.ndarray  # Embedding of weighted_context for real-time operations
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate embeddings on creation."""
        if self.embedding_384 is None:
            raise ValueError("384-dimensional embedding is required")
        
        if not isinstance(self.embedding_384, np.ndarray):
            raise TypeError("embedding_384 must be a numpy array")
            
        if self.embedding_384.shape[0] != 384:
            raise ValueError(f"embedding_384 must be 384-dimensional, got {self.embedding_384.shape[0]}")
    
    def cleanup(self) -> None:
        """
        Explicitly destroy embeddings after use.
        
        This method should be called after all services have processed the message
        to ensure embeddings are not retained in memory.
        """
        logger.debug(f"Cleaning up embeddings for message from user {self.user_id}")
        self.embedding_384 = None
        # Clear the arrays from memory
        import gc
        gc.collect()