"""OpenAI text embedding generation with connection pooling and error handling."""

import os
import logging
import numpy as np
import time
from typing import List, Union, Dict, Any
import openai
from openai import OpenAI
from utils import http_client



logger = logging.getLogger(__name__)


class OpenAIEmbeddingModel:
    
    def __init__(self, api_key: str = None, model: str = "text-embedding-3-small"):
        try:
            if api_key is None:
                from clients.vault_client import get_api_key
                api_key = get_api_key('openai_embeddings_key')
            
            self.api_key = api_key
            self.model = model
            
            try:
                http_client_instance = http_client.Client(
                    limits=http_client.Limits(
                        max_keepalive_connections=10,
                        max_connections=20,
                        keepalive_expiry=300  # 5 minutes
                    ),
                    timeout=http_client.Timeout(
                        connect=10.0,
                        read=60.0,
                        write=10.0,
                        pool=5.0
                    )
                )
                
                self.client = OpenAI(
                    api_key=self.api_key,
                    http_client=http_client_instance
                )
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")
                raise RuntimeError(f"Failed to initialize OpenAI client: {e}")
            
            self.embedding_dims = {
                "text-embedding-3-small": 1024,
                "text-embedding-3-large": 3072,
                "text-embedding-ada-002": 1024
            }
            
            if model not in self.embedding_dims:
                raise ValueError(f"Unsupported embedding model: {model}. Supported: {list(self.embedding_dims.keys())}")
            
            self.embedding_dim = self.embedding_dims[model]
            
            logger.info(f"Initialized OpenAI embedding model: {model} (dim={self.embedding_dim})")
        except Exception:
            raise
    
    def encode(self, texts: Union[str, List[str]], batch_size: int = 32) -> np.ndarray:
        try:
            if isinstance(texts, str):
                texts = [texts]
                single_input = True
            else:
                single_input = False
            
            if not texts:
                raise ValueError("No texts provided for encoding")
            
            for i, text in enumerate(texts):
                if not text or not text.strip():
                    raise ValueError(f"Empty text at index {i}")
            
            all_embeddings = []
            
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i + batch_size]
                
                try:
                    start_time = time.time()
                    batch_info = f"batch {i//batch_size + 1}/{(len(texts) + batch_size - 1)//batch_size}"
                    
                    logger.info(f"OpenAI API request - {len(batch_texts)} texts, {batch_info}")
                    
                    response = self.client.embeddings.create(
                        model=self.model,
                        input=batch_texts,
                        encoding_format="float",
                        dimensions=self.embedding_dim
                    )
                    
                    end_time = time.time()
                    time_in_flight = (end_time - start_time) * 1000  # Convert to milliseconds
                    logger.info(f"OpenAI API response - {len(response.data)} embeddings, {time_in_flight:.1f}ms")
                    
                    batch_embeddings = []
                    for embedding_obj in response.data:
                        embedding = np.array(embedding_obj.embedding, dtype=np.float32)
                        
                        if embedding.shape[0] != self.embedding_dim:
                            raise RuntimeError(f"Unexpected embedding dimension: got {embedding.shape[0]}, expected {self.embedding_dim}")
                        
                        batch_embeddings.append(embedding)
                    
                    all_embeddings.extend(batch_embeddings)
                    
                except openai.RateLimitError as e:
                    logger.warning(f"OpenAI API rate limit exceeded: {e}")
                    raise
                except openai.AuthenticationError as e:
                    logger.error(f"OpenAI API authentication failed: {e}")
                    raise
                except openai.APIError as e:
                    logger.error(f"OpenAI API error: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error during embedding generation: {e}")
                    raise
            
            result = np.array(all_embeddings, dtype=np.float32)
            
            if single_input:
                return result[0]
            
            return result
        except Exception:
            raise
    
    def get_dimension(self) -> int:
        return self.embedding_dim
    
    def test_connection(self) -> Dict[str, Any]:
        try:
            logger.info("Testing OpenAI API connection")
            test_embedding = self.encode("Hello, world!")
            
            return {
                "status": "success",
                "model": self.model,
                "embedding_dim": self.embedding_dim,
                "test_embedding_shape": test_embedding.shape,
                "test_embedding_norm": float(np.linalg.norm(test_embedding)),
                "api_accessible": True
            }
        except Exception as e:
            return {
                "status": "error",
                "model": self.model,
                "error": str(e),
                "api_accessible": False
            }
    
    def close(self):
        try:
            if hasattr(self.client, '_client') and hasattr(self.client._client, 'close'):
                self.client._client.close()
                logger.info("Closed OpenAI HTTP client connections")
        except Exception as e:
                logger.warning(f"Error closing OpenAI client: {e}")
    
    def __del__(self):
        self.close()