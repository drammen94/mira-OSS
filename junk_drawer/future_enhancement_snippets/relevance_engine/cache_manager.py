"""
Cache manager consolidating static training embeddings, classifier state, and file hash tracking.
"""
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Any, Optional


class CacheManager:
    """Manages static training embeddings (22MB), classifier state, and file hash tracking."""
    
    def __init__(self, cache_dir: str):
        self.logger = logging.getLogger("cache_manager")
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Static embedding cache for tool activation phrases (read-only after init)
        self.embedding_cache: Dict[str, List[float]] = {}
        self.classifier_state_cache = None
        self.file_hash_cache: Dict[str, str] = {}
    
    def get_embedding(self, text: str) -> Optional[List[float]]:
        return self.embedding_cache.get(text)
    
    def store_embedding(self, text: str, embedding: List[float]) -> None:
        self.embedding_cache[text] = embedding
    
    def load_embedding_cache(self) -> None:
        embedding_cache_file = os.path.join(self.cache_dir, "embedding_cache.json")
        
        if os.path.exists(embedding_cache_file):
            try:
                with open(embedding_cache_file, 'r') as f:
                    self.embedding_cache = json.load(f)
            except (json.JSONDecodeError, OSError, IOError) as e:
                self.logger.warning(f"Embedding cache corrupted, starting fresh: {e}")
                self.embedding_cache = {}
    
    def save_embedding_cache(self) -> None:
        embedding_cache_file = os.path.join(self.cache_dir, "embedding_cache.json")
        
        try:
            with open(embedding_cache_file, 'w') as f:
                json.dump(self.embedding_cache, f)
        except (OSError, IOError, json.JSONEncodeError) as e:
            self.logger.error(f"Failed to save embedding cache: {e}")
    
    def load_classifier_state(self) -> Optional[Dict[str, Any]]:
        cache_file = os.path.join(self.cache_dir, "classifier_state.json")
        
        if not os.path.exists(cache_file):
            return None
            
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            self.classifier_state_cache = cache_data
            return cache_data.get('classifiers', {})
        except (json.JSONDecodeError, OSError, IOError) as e:
            self.logger.warning(f"Classifier state cache corrupted: {e}")
            return None
    
    def save_classifier_state(self, classifiers: Dict[str, Dict[str, Any]]) -> None:
        cache_file = os.path.join(self.cache_dir, "classifier_state.json")
        
        try:
            cache_data = {
                'classifiers': classifiers,
                'timestamp': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f)
        except (OSError, IOError, json.JSONEncodeError) as e:
            self.logger.error(f"Failed to save classifier state: {e}")
    
    def get_file_hash(self, file_path: str) -> Optional[str]:
        return self.file_hash_cache.get(file_path)
    
    def store_file_hash(self, file_path: str, file_hash: str) -> None:
        self.file_hash_cache[file_path] = file_hash
    
    def load_file_hashes(self, tools_data_dir: str) -> Dict[str, str]:
        """Updates internal cache with loaded hashes and returns them."""
        hashes_file = os.path.join(tools_data_dir, "classifier_file_hashes.json")
        
        if os.path.exists(hashes_file):
            try:
                with open(hashes_file, 'r') as f:
                    loaded_hashes = json.load(f)
                    # Update internal cache while returning loaded data
                    self.file_hash_cache.update(loaded_hashes)
                    return loaded_hashes
            except (json.JSONDecodeError, OSError, IOError) as e:
                self.logger.warning(f"File hash cache corrupted: {e}")
        
        return {}
    
    def save_file_hashes(self, tools_data_dir: str) -> None:
        hashes_file = os.path.join(tools_data_dir, "classifier_file_hashes.json")
        
        try:
            with open(hashes_file, 'w') as f:
                json.dump(self.file_hash_cache, f)
        except (OSError, IOError, json.JSONEncodeError) as e:
            self.logger.error(f"Failed to save file hashes: {e}")
    
    def clear_all_caches(self) -> None:
        self.embedding_cache.clear()
        self.file_hash_cache.clear()
        self.classifier_state_cache = None
    
    def get_cache_stats(self) -> Dict[str, int]:
        return {
            "embedding_cache_size": len(self.embedding_cache),
            "file_hash_cache_size": len(self.file_hash_cache),
            "classifier_state_loaded": 1 if self.classifier_state_cache else 0
        }