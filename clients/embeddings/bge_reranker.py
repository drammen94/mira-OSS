import logging
import os
from typing import List, Union, Optional
import numpy as np
from utils import http_client
from tqdm import tqdm
import threading
from concurrent.futures import ProcessPoolExecutor

# Single reranker pool instance (singleton pattern)
_bge_reranker_pool_instance = None
_bge_reranker_pool_lock = threading.Lock()



class BaseONNXModel:
    def __init__(self, model_name: str, cache_dir: str, thread_limit: int):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.thread_limit = thread_limit
        self.session = None
        self.tokenizer = None
    
    def _load_tokenizer(self):
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
    
    def _download_file(self, url: str, dest_path: str):
        with http_client.stream('GET', url) as response:
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(dest_path, 'wb') as f:
                with tqdm(total=total_size, unit='B', unit_scale=True, desc="Downloading") as pbar:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))


class BGEReranker(BaseONNXModel):
    
    def __init__(self,
                 model_name: str = "BAAI/bge-reranker-base",
                 model_path: Optional[str] = None,
                 use_fp16: bool = True,
                 cache_dir: Optional[str] = None,
                 thread_limit: Optional[int] = 4):
        self.logger = logging.getLogger("bge_reranker")
        cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "bge_models")
        super().__init__(model_name, cache_dir, thread_limit)
        
        self.use_fp16 = use_fp16
        
        # Model paths
        if model_path:
            self.model_path = model_path
        else:
            model_file = "reranker_fp16.onnx" if use_fp16 else "reranker.onnx"
            self.model_path = os.path.join(self.cache_dir, model_name.replace("/", "_"), model_file)
        
        self._load_model()
    
    def _load_model(self):
        try:
            import onnxruntime as ort
            
            # Download/convert model if not exists
            if not os.path.exists(self.model_path):
                self._convert_to_onnx()
            
            # Load tokenizer
            self._load_tokenizer()
            
            # Create ONNX session
            self._create_onnx_session()
            
            self.logger.info(f"BGE reranker loaded from {self.model_path}")
            
        except ImportError as e:
            missing_package = "onnxruntime"
            if "transformers" in str(e):
                missing_package = "transformers"
            raise ImportError(
                f"Required package '{missing_package}' not installed. "
                f"Run: pip install {missing_package}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to load BGE reranker ONNX model: {str(e)}") from e
    
    def _convert_to_onnx(self):
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
            
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            
            model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir
            )
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir
            )
            
            # Prepare dummy input
            dummy_input = tokenizer(
                [["query", "passage"]],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt"
            )
            
            # Export to ONNX
            input_names = ['input_ids', 'attention_mask']
            output_names = ['logits']
            dynamic_axes = {
                'input_ids': {0: 'batch_size', 1: 'sequence'},
                'attention_mask': {0: 'batch_size', 1: 'sequence'},
                'logits': {0: 'batch_size'}
            }
            
            # Add token_type_ids if present
            model_inputs = (dummy_input['input_ids'], dummy_input['attention_mask'])
            if 'token_type_ids' in dummy_input:
                model_inputs = model_inputs + (dummy_input['token_type_ids'],)
                input_names.append('token_type_ids')
                dynamic_axes['token_type_ids'] = {0: 'batch_size', 1: 'sequence'}
            
            torch.onnx.export(
                model,
                model_inputs,
                self.model_path,
                export_params=True,
                opset_version=14,
                do_constant_folding=True,
                input_names=input_names,
                output_names=output_names,
                dynamic_axes=dynamic_axes
            )
            
            # Convert to FP16 if requested
            if self.use_fp16:
                self._convert_to_fp16()
            
            # Save tokenizer
            tokenizer.save_pretrained(os.path.dirname(self.model_path))
            
            self.logger.info(f"Reranker converted to ONNX format at {self.model_path}")
            
        except ImportError as e:
            raise ImportError(
                "Required packages for ONNX conversion not installed. "
                "Run: pip install torch transformers"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to convert reranker to ONNX: {str(e)}") from e
    
    def _convert_to_fp16(self):
        try:
            import onnx
            from onnxconverter_common import float16
            
            model = onnx.load(self.model_path)
            model_fp16 = float16.convert_float_to_float16(model)
            onnx.save(model_fp16, self.model_path)
            
            
        except ImportError:
            self.logger.warning("onnxconverter-common not installed, skipping FP16 conversion")
        except Exception as e:
            self.logger.warning(f"Failed to convert to FP16: {str(e)}")
    
    def rerank(self,
               query: str,
               passages: List[str],
               batch_size: int = 32,
               return_scores: bool = True) -> Union[List[int], List[tuple]]:
        if not passages:
            return []
        
        all_scores = []
        
        # Process in batches
        for i in range(0, len(passages), batch_size):
            batch_passages = passages[i:i + batch_size]
            
            # Create query-passage pairs
            pairs = [[query, passage] for passage in batch_passages]
            
            # Tokenize
            encoded_input = self.tokenizer(
                pairs,
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
            
            # Get scores and apply sigmoid
            logits = outputs[0]
            scores = 1 / (1 + np.exp(-logits))  # Sigmoid
            
            # Handle both single and multi-dimensional outputs
            if len(scores.shape) > 1:
                scores = scores.squeeze(-1)
            
            all_scores.extend(scores.tolist())
        
        # Ensure all_scores is a flat list
        if isinstance(all_scores[0], list):
            all_scores = [score[0] if isinstance(score, list) else score for score in all_scores]
        
        # Create index-score pairs
        indexed_scores = [(idx, score) for idx, score in enumerate(all_scores)]
        
        # Sort by score (descending)
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        
        if return_scores:
            return indexed_scores
        else:
            return [idx for idx, _ in indexed_scores]
    
    def compute_relevance_scores(self,
                                 query: str,
                                 passages: List[str],
                                 batch_size: int = 32) -> np.ndarray:
        scores_with_indices = self.rerank(
            query, passages, batch_size=batch_size, return_scores=True
        )
        
        # Extract scores in original order
        scores = [0.0] * len(passages)
        for idx, score in scores_with_indices:
            scores[idx] = score
        
        return np.array(scores)
    
    def close(self):
        if hasattr(self, 'session') and self.session:
            self.session = None
        if hasattr(self, 'tokenizer'):
            self.tokenizer = None


# Process worker functions for ProcessPoolExecutor
# These must be module-level functions to be pickleable
_process_reranker = None

def _init_reranker_process(model_name, model_path, use_fp16, cache_dir, thread_limit):
    """Initialize BGE reranker in process worker."""
    global _process_reranker
    _process_reranker = BGEReranker(
        model_name=model_name,
        model_path=model_path,
        use_fp16=use_fp16,
        cache_dir=cache_dir,
        thread_limit=thread_limit
    )
    # Set process title for debugging
    try:
        import setproctitle
        setproctitle.setproctitle(f"bge_reranker_worker")
    except ImportError:
        pass

def _rerank_in_process(query, passages, batch_size, return_scores):
    """Execute rerank in process worker."""
    global _process_reranker
    if _process_reranker is None:
        raise RuntimeError("Reranker not initialized in process")
    return _process_reranker.rerank(query, passages, batch_size, return_scores)


class BGERerankerPool:
    """
    Adaptive reranker pool that uses thread-safe single instance or process pool.
    
    - pool_size=1: Thread-safe single instance (low overhead, prevents crashes)
    - pool_size>1: ProcessPoolExecutor for true parallelism
    """
    
    def __init__(self,
                 pool_size: int = 4,
                 model_name: str = "BAAI/bge-reranker-base",
                 model_path: Optional[str] = None,
                 use_fp16: bool = True,
                 cache_dir: Optional[str] = None,
                 thread_limit: Optional[int] = 4):
        """
        Initialize adaptive BGE reranker pool.
        
        Args:
            pool_size: Number of reranker instances (1=thread-safe single, >1=process pool)
            model_name: BGE reranker model name
            model_path: Optional path to local model file
            use_fp16: Whether to use FP16 optimization
            cache_dir: Cache directory for model files
            thread_limit: Thread limit for each reranker instance
        """
        self.logger = logging.getLogger("bge_reranker_pool")
        self.pool_size = pool_size
        self.model_args = {
            'model_name': model_name,
            'model_path': model_path,
            'use_fp16': use_fp16,
            'cache_dir': cache_dir,
            'thread_limit': thread_limit
        }
        self._shutdown = False
        
        if pool_size == 1:
            # Single instance with thread lock (prevents race conditions)
            self.logger.info("Creating thread-safe single BGE reranker instance")
            self._reranker = BGEReranker(**self.model_args)
            self._lock = threading.Lock()
            self.executor = None
            self.logger.info("Single BGE reranker initialized with thread safety")
        else:
            # Process pool for true parallelism
            self.logger.info(f"Creating BGE reranker process pool with {pool_size} workers")
            self._reranker = None
            self._lock = None
            self.executor = ProcessPoolExecutor(
                max_workers=pool_size,
                initializer=_init_reranker_process,
                initargs=(model_name, model_path, use_fp16, cache_dir, thread_limit)
            )
            self.logger.info(f"BGE reranker process pool initialized with {pool_size} workers")
    
    def rerank(self,
               query: str,
               passages: List[str],
               batch_size: int = 32,
               return_scores: bool = True) -> Union[List[int], List[tuple]]:
        """
        Rerank passages using adaptive pool strategy.
        
        Args:
            query: Search query
            passages: List of passages to rerank
            batch_size: Batch size for processing
            return_scores: Whether to return scores with indices
            
        Returns:
            Ranked results as list of indices or (index, score) tuples
        """
        if self._shutdown:
            raise RuntimeError("BGE reranker pool has been shut down")
        
        if not passages:
            return []
        
        if self.pool_size == 1:
            # Thread-safe single instance
            with self._lock:
                return self._reranker.rerank(query, passages, batch_size, return_scores)
        else:
            # Process pool for parallelism
            future = self.executor.submit(
                _rerank_in_process, query, passages, batch_size, return_scores
            )
            return future.result()
    
    def compute_relevance_scores(self,
                                 query: str,
                                 passages: List[str],
                                 batch_size: int = 32) -> np.ndarray:
        """
        Compute relevance scores using adaptive pool strategy.
        
        Args:
            query: Search query
            passages: List of passages to score
            batch_size: Batch size for processing
            
        Returns:
            Array of relevance scores
        """
        if self._shutdown:
            raise RuntimeError("BGE reranker pool has been shut down")
        
        if self.pool_size == 1:
            # Thread-safe single instance
            with self._lock:
                return self._reranker.compute_relevance_scores(query, passages, batch_size)
        else:
            # Use rerank and extract scores
            scores_with_indices = self.rerank(query, passages, batch_size, return_scores=True)
            # Extract scores in original order
            scores = [0.0] * len(passages)
            for idx, score in scores_with_indices:
                scores[idx] = score
            return np.array(scores)
    
    def close(self):
        """
        Shut down the pool and clean up resources.
        """
        if self._shutdown:
            return
        
        self.logger.info("Shutting down BGE reranker pool")
        self._shutdown = True
        
        if self.pool_size == 1:
            # Close single instance
            if self._reranker:
                self._reranker.close()
                self.logger.info("Single BGE reranker instance closed")
        else:
            # Shutdown process pool
            if self.executor:
                self.executor.shutdown(wait=True)
                self.logger.info(f"BGE reranker process pool shut down ({self.pool_size} workers)")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Singleton factory function
def get_bge_reranker(model_name: str = "BAAI/bge-reranker-base",
                     model_path: Optional[str] = None,
                     use_fp16: bool = False,
                     cache_dir: Optional[str] = None,
                     thread_limit: Optional[int] = 4,
                     pool_size: Optional[int] = None) -> BGERerankerPool:
    """
    Get or create a singleton BGE reranker pool.
    
    Returns a thread-safe pool of reranker instances that can handle
    concurrent requests without blocking.
    
    Args:
        model_name: BGE reranker model name
        model_path: Optional path to local model file
        use_fp16: Whether to use FP16 optimization (default True)
        cache_dir: Cache directory for model files
        thread_limit: Thread limit for each reranker instance
        pool_size: Size of the reranker pool (defaults to config value)
        
    Returns:
        BGERerankerPool instance (singleton)
    """
    global _bge_reranker_pool_instance, _bge_reranker_pool_lock
    
    # Check if pool exists without lock first (double-checked locking)
    if _bge_reranker_pool_instance is not None:
        return _bge_reranker_pool_instance
    
    with _bge_reranker_pool_lock:
        # Check again inside lock
        if _bge_reranker_pool_instance is not None:
            return _bge_reranker_pool_instance
        
        # Get pool size from config if not specified
        if pool_size is None:
            from config.config_manager import config
            pool_size = config.embeddings.reranker_pool_size
        
        # Create new pool
        logger = logging.getLogger("bge_embeddings")
        logger.info(f"Creating BGE reranker pool with {pool_size} instances of {model_name}")
        
        pool = BGERerankerPool(
            pool_size=pool_size,
            model_name=model_name,
            model_path=model_path,
            use_fp16=use_fp16,
            cache_dir=cache_dir,
            thread_limit=thread_limit
        )
        
        _bge_reranker_pool_instance = pool
        logger.info(f"BGE reranker pool created and cached")
        
        return pool


