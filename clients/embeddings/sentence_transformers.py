"""
Sentence Transformers models for fast, lightweight embeddings.

Provides ONNX-optimized implementations of smaller transformer models
like all-MiniLM-L6-v2 for real-time embedding tasks.
"""
import logging
import os
from typing import List, Union, Optional, Tuple
import numpy as np
from pathlib import Path
import threading

# Global model instances (singleton pattern)
_minilm_model_instances = {}
_minilm_model_lock = threading.Lock()

logger = logging.getLogger(__name__)


class AllMiniLMModel:
    """
    All-MiniLM-L6-v2 model for fast 384-dimensional embeddings.
    
    Optimized for real-time operations with:
    - 384 dimensions (vs 1024 for deep embeddings)
    - 512 max tokens (local model constraint) 
    - ~1ms inference time (very fast for real-time operations)
    """
    
    def __init__(self,
                 model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
                 cache_dir: Optional[str] = None,
                 thread_limit: int = 2):
        """
        Initialize All-MiniLM model.
        
        Args:
            model_name: Model name (default: all-MiniLM-L6-v2)
            cache_dir: Directory for caching model files
            thread_limit: Number of threads for inference
        """
        self.logger = logging.getLogger("all_minilm")
        self.model_name = model_name
        self.cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "sentence_transformers")
        self.thread_limit = thread_limit
        
        # Model will be loaded on first use
        self.session = None
        self.tokenizer = None
        
        # Model paths
        model_file = "model.onnx"
        self.model_path = os.path.join(self.cache_dir, model_name.replace("/", "_"), model_file)
        
        self._load_model()
    
    def _load_model(self):
        """Load or download the ONNX model."""
        try:
            import onnxruntime as ort
            
            # Download/convert model if not exists
            if not os.path.exists(self.model_path):
                self._convert_to_onnx()
            
            # Load tokenizer
            self._load_tokenizer()
            
            # Create ONNX session
            self._create_onnx_session()
            
            self.logger.info(f"All-MiniLM model loaded from {self.model_path}")
            
        except ImportError as e:
            missing_package = "onnxruntime"
            if "transformers" in str(e):
                missing_package = "transformers"
            self.logger.error(f"Required package '{missing_package}' not installed")
            raise ImportError(f"Required package '{missing_package}' not installed. Run: pip install {missing_package}")
        except Exception as e:
            self.logger.error(f"Failed to load All-MiniLM model: {str(e)}")
            raise RuntimeError(f"Failed to load All-MiniLM ONNX model: {str(e)}")
    
    def _load_tokenizer(self):
        """Load the tokenizer."""
        from transformers import AutoTokenizer
        
        tokenizer_path = os.path.dirname(self.model_path)
        tokenizer_config_path = os.path.join(tokenizer_path, "tokenizer_config.json")
        
        if os.path.exists(tokenizer_config_path):
            self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        else:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir
            )
            self.tokenizer.save_pretrained(tokenizer_path)
    
    def _create_onnx_session(self):
        """Create ONNX inference session."""
        import onnxruntime as ort
        
        providers = ['CPUExecutionProvider']
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = self.thread_limit
        sess_options.inter_op_num_threads = 1
        
        self.session = ort.InferenceSession(
            self.model_path,
            sess_options=sess_options,
            providers=providers
        )
    
    def _convert_to_onnx(self):
        """Convert HuggingFace model to ONNX format."""
        try:
            from transformers import AutoModel
            from optimum.onnxruntime import ORTModelForFeatureExtraction
            import torch
            
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            
            # Convert to ONNX using Optimum
            temp_dir = os.path.dirname(self.model_path)
            ort_model = ORTModelForFeatureExtraction.from_pretrained(
                self.model_name,
                export=True,
                cache_dir=self.cache_dir
            )
            
            # Save ONNX model
            ort_model.save_pretrained(temp_dir)
            
            # Rename if needed
            original_path = os.path.join(temp_dir, "model.onnx")
            if original_path != self.model_path and os.path.exists(original_path):
                os.rename(original_path, self.model_path)
            
            self.logger.info(f"Model converted to ONNX format at {self.model_path}")
            
        except ImportError as e:
            raise ImportError(
                "Required packages for ONNX conversion not installed. "
                "Run: pip install optimum[onnxruntime] torch transformers"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to convert model to ONNX: {str(e)}")
    
    def encode(self,
               texts: Union[str, List[str]],
               batch_size: int = 32,
               show_progress: bool = False) -> np.ndarray:
        """
        Generate embeddings for texts.
        
        Always returns normalized embeddings for cosine similarity.
        
        Args:
            texts: Single text or list of texts to encode
            batch_size: Batch size for encoding
            show_progress: Whether to show progress (ignored)
            
        Returns:
            Normalized embeddings as numpy array
        """
        # Handle corruption recovery
        if self.tokenizer is None or self.session is None:
            self.logger.warning("Model corrupted, attempting reinitialization...")
            try:
                self._load_model()
                if self.tokenizer is None or self.session is None:
                    raise RuntimeError("Reinitialization failed to restore model components")
            except Exception as e:
                raise RuntimeError(f"Model reinitialization failed: {e}")
        
        if isinstance(texts, str):
            texts = [texts]
            single_text = True
        else:
            single_text = False
        
        all_embeddings = []
        
        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            
            # Tokenize (max 512 tokens for MiniLM)
            encoded_input = self.tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors='np'
            )
            
            # Prepare inputs
            ort_inputs = {
                'input_ids': encoded_input['input_ids'],
                'attention_mask': encoded_input['attention_mask']
            }
            
            # Add token_type_ids if model expects it
            if 'token_type_ids' in [inp.name for inp in self.session.get_inputs()]:
                ort_inputs['token_type_ids'] = encoded_input.get(
                    'token_type_ids',
                    np.zeros_like(encoded_input['input_ids'])
                )
            
            # Run inference
            outputs = self.session.run(None, ort_inputs)
            
            # Extract embeddings (last hidden state)
            last_hidden_state = outputs[0]
            
            # Mean pooling
            embeddings = self._mean_pooling(
                last_hidden_state,
                encoded_input['attention_mask']
            )
            
            # Always normalize for cosine similarity
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / (norms + 1e-10)
            
            all_embeddings.append(embeddings)
        
        # Concatenate all batches
        embeddings = np.vstack(all_embeddings)
        
        # Return single embedding if single input
        if single_text:
            return embeddings[0]
        
        return embeddings
    
    def _mean_pooling(self, token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """Perform mean pooling on token embeddings."""
        input_mask_expanded = np.expand_dims(attention_mask, -1)
        sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
        sum_mask = np.sum(input_mask_expanded, axis=1)
        sum_mask = np.clip(sum_mask, a_min=1e-9, a_max=None)
        return sum_embeddings / sum_mask
    
    def get_dimension(self) -> int:
        """Get embedding dimension (384 for All-MiniLM)."""
        return 384
    
    def close(self):
        """Clean up resources."""
        if hasattr(self, 'session') and self.session:
            self.session = None
        if hasattr(self, 'tokenizer'):
            self.tokenizer = None


def get_all_minilm_model(model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
                         cache_dir: Optional[str] = None,
                         thread_limit: int = 2) -> AllMiniLMModel:
    """
    Get or create a singleton All-MiniLM model instance.
    
    Uses the same pattern as BGE models - one instance per unique configuration.
    
    Args:
        model_name: Model name (default: all-MiniLM-L6-v2)
        cache_dir: Cache directory for model files
        thread_limit: Thread limit for inference
        
    Returns:
        AllMiniLMModel instance
    """
    global _minilm_model_instances, _minilm_model_lock
    
    # Create a unique key for this configuration
    key = (model_name, cache_dir, thread_limit)
    
    # Check if instance exists without lock first
    if key in _minilm_model_instances:
        return _minilm_model_instances[key]
    
    with _minilm_model_lock:
        # Check again inside lock
        if key in _minilm_model_instances:
            return _minilm_model_instances[key]
        
        # Create new instance
        logger.info(f"Creating new All-MiniLM model singleton for {model_name}")
        
        model = AllMiniLMModel(
            model_name=model_name,
            cache_dir=cache_dir,
            thread_limit=thread_limit
        )
        
        _minilm_model_instances[key] = model
        logger.info(f"All-MiniLM model singleton created and cached")
        
        return model