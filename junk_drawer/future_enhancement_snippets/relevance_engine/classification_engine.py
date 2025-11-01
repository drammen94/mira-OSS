"""
Unified Classification Engine for tool relevance determination.

This module consolidates MultiLabelClassifier + EmbeddingMatrix + embedding
cache operations into a single engine. ALL EXISTING BUGS AND COMPLEXITY ARE
PRESERVED during this faithful merge.

CRITICAL BUGS PRESERVED:
- BUG #2: Division by Zero in Matrix Operations (line 156 in original)
- BUG #3: Division by Zero in Classifier Training (multiple locations)
- BUG #5: Thread Safety - Concurrent Dictionary Modification
- Matrix dimension mismatch without validation
- All thread safety issues and error handling gaps
"""
import logging
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
from numpy.typing import NDArray

from .cache_manager import CacheManager
from cns.core.embedded_message import EmbeddedMessage


class ClassificationEngine:
    """
    Unified classification engine for determining tool relevance from messages.
    
    This consolidates:
    - MultiLabelClassifier: one-vs-rest classification with MiniLM embeddings
    - EmbeddingMatrix: precomputed matrices for efficient batch operations
    - Embedding caching: via CacheManager integration
    
    ALL ORIGINAL BUGS AND COMPLEXITY ARE PRESERVED.
    """
    
    def __init__(self, thread_limit: int = 2, cache_dir: str = "data/classifier", model=None):
        """
        Initialize the ClassificationEngine.
        
        Args:
            thread_limit: Maximum number of threads to use for classifier operations
            cache_dir: Directory for storing cached classifier data
            model: Pre-loaded ONNX embedding model to use (for sharing)
        """
        self.logger = logging.getLogger("classification_engine")
        self.cache_dir = cache_dir
        self.thread_limit = thread_limit
        self.model = model
        self.thread_semaphore = threading.Semaphore(thread_limit)
        
        # Core classification data (PRESERVING thread safety bugs)
        self.classifiers: Dict[str, Dict[str, Any]] = {}
        self.examples: List[Dict[str, Any]] = []
        
        # Precomputed tool embeddings for matrix operations (PRESERVING matrix bugs)
        self.tool_embeddings_matrix = None
        self.tool_names_order = []
        
        # Cache manager for unified caching
        self.cache_manager = CacheManager(cache_dir)
        
        # Initialize the model if not provided
        if self.model is None:
            self._initialize_model()
    
    def _initialize_model(self) -> None:
        """
        Initialize the ONNX embedding model.
        
        This method is no longer needed as the model is passed in during initialization.
        """
        if self.model is None:
            self.logger.error("No ONNX model provided")
        else:
            self.logger.info("Using provided ONNX embedding model")
    
    def calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate semantic similarity between two text strings.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score (0.0-1.0) where 1.0 is identical
        """
        try:
            # Get embeddings
            embedding1 = self._compute_embedding(text1)
            embedding2 = self._compute_embedding(text2)
            
            if embedding1 is None or embedding2 is None:
                return 0.0
            
            # Normalize embeddings
            emb1_array = np.array(embedding1)
            emb2_array = np.array(embedding2)
            
            norm1 = np.linalg.norm(emb1_array)
            norm2 = np.linalg.norm(emb2_array)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            emb1_norm = emb1_array / norm1
            emb2_norm = emb2_array / norm2
            
            # Calculate cosine similarity
            similarity = float(np.dot(emb1_norm, emb2_norm))
            
            # Ensure value is in range [0, 1]
            return max(0.0, min(similarity, 1.0))
        
        except Exception as e:
            self.logger.error(f"Error calculating text similarity: {e}")
            return 0.0
    
    def train_classifier(self, examples: List[Dict[str, Any]], force_retrain: bool = False) -> None:
        """
        Train the one-vs-rest classifier using provided examples.
        
        This method either loads a cached classifier state or trains new classifiers
        based on the provided examples.
        
        Args:
            examples: List of dictionaries containing tool_name/tool_names and query pairs
            force_retrain: Whether to force retraining even if a cached state exists
        """
        self.logger.info(f"Training one-vs-rest classifier with {len(examples)} examples")
        
        # Store examples for reference
        self.examples = examples
        
        # Load embedding cache
        self.cache_manager.load_embedding_cache()
        
        try:
            # Check if we have a cached classifier state
            if not force_retrain:
                cached_classifiers = self.cache_manager.load_classifier_state()
                
                if cached_classifiers:
                    self.classifiers = cached_classifiers
                    self.logger.info(f"Loaded {len(self.classifiers)} classifiers from cache")
                    return
        except Exception as e:
            self.logger.warning(f"Error loading cached classifier data: {e}")
            # Reset caches if there was an error
            self.classifiers = {}
        
        # If we get here, we need to train new classifiers
        self.logger.info("Training new classifiers")
        self.classifiers = {}
        
        # First, prepare all unique tool names from examples
        tool_names = set()
        
        for example in examples:
            # Handle both single tool and multiple tools in examples
            if "tool_name" in example:
                tool_names.add(example["tool_name"])
            elif "tool_names" in example and isinstance(example["tool_names"], list):
                for tool in example["tool_names"]:
                    tool_names.add(tool)
        
        self.logger.info(f"Found {len(tool_names)} unique tools in examples")
        
        # Compute embeddings for all unique queries (to avoid recomputing)
        self._precompute_embeddings(examples)
        
        # For each tool, create its one-vs-rest classifier
        for tool_name in tool_names:
            self.logger.info(f"Training classifier for {tool_name}")
            
            # Collect positive and negative examples for this tool
            positive_examples = []
            negative_examples = []
            
            for example in examples:
                if "query" not in example:
                    continue
                
                query = example["query"]
                
                # Determine if this example is positive or negative for this tool
                is_positive = False
                
                if "tool_name" in example and example["tool_name"] == tool_name:
                    is_positive = True
                elif "tool_names" in example and isinstance(example["tool_names"], list) and tool_name in example["tool_names"]:
                    is_positive = True
                
                if is_positive:
                    positive_examples.append(query)
                else:
                    negative_examples.append(query)
            
            # Only create classifier if we have positive examples
            if not positive_examples:
                self.logger.warning(f"No positive examples found for {tool_name}, skipping")
                continue
            
            self.logger.info(f"Tool {tool_name}: {len(positive_examples)} positive, {len(negative_examples)} negative examples")
            
            # Create classifier data
            classifier_data = self._create_tool_classifier(tool_name, positive_examples, negative_examples)
            
            if classifier_data:
                self.classifiers[tool_name] = classifier_data
        
        # Cache the classifier state and embeddings
        self.cache_manager.save_classifier_state(self.classifiers)
        self.cache_manager.save_embedding_cache()
    
    def _precompute_embeddings(self, examples: List[Dict[str, Any]]) -> None:
        """
        Precompute embeddings for all unique queries in the examples.
        
        Args:
            examples: List of dictionaries containing tool_name/tool_names and query pairs
        """
        # Collect unique queries
        unique_queries = set()
        for example in examples:
            if "query" in example:
                unique_queries.add(example["query"])
        
        # Filter out queries that are already in the embedding cache
        queries_to_compute = [q for q in unique_queries if self.cache_manager.get_embedding(q) is None]
        
        if not queries_to_compute:
            self.logger.info("All query embeddings already in cache")
            return
        
        self.logger.info(f"Precomputing embeddings for {len(queries_to_compute)} unique queries")
        
        # Compute embeddings in batches to save time
        import numpy as np
        batch_size = 16  # Adjust based on available memory
        
        for i in range(0, len(queries_to_compute), batch_size):
            batch = queries_to_compute[i:i+batch_size]
            
            try:
                # Limit concurrent computations
                with self.thread_semaphore:
                    # Skip long texts that could cause memory issues
                    valid_batch = [text for text in batch if len(text) <= 8192]
                    
                    if not valid_batch:
                        continue
                    
                    # Compute embeddings for the batch
                    if self.model is None:
                        raise RuntimeError("No embedding model provided to ClassificationEngine")
                    
                    # Use encode_realtime for tool classification (fast path)
                    embeddings = self.model.encode_realtime(valid_batch)
                    
                    # Store in cache
                    for j, text in enumerate(valid_batch):
                        self.cache_manager.store_embedding(text, embeddings[j].tolist())
                
                self.logger.debug(f"Computed embeddings for batch {i//batch_size + 1}/{(len(queries_to_compute) + batch_size - 1)//batch_size}")
            
            except Exception as e:
                self.logger.error(f"Error computing embeddings for batch: {e}")
        
        self.logger.info(f"Precomputed embeddings for {len(queries_to_compute)} queries")
    
    def _create_tool_classifier(self, tool_name: str, positive_examples: List[str], negative_examples: List[str]) -> Optional[Dict[str, Any]]:
        """
        Create a one-vs-rest classifier for a specific tool.
        
        Args:
            tool_name: Name of the tool
            positive_examples: List of queries that should use this tool
            negative_examples: List of queries that should not use this tool
            
        Returns:
            Dictionary containing classifier data and parameters or None if creation fails
        """
        try:
            # Get embeddings for positive examples from cache
            positive_embeddings: List[List[float]] = []
            for example in positive_examples:
                cached_embedding = self.cache_manager.get_embedding(example)
                if cached_embedding is not None:
                    positive_embeddings.append(cached_embedding)
            
            # Get embeddings for negative examples from cache
            negative_embeddings: List[List[float]] = []
            for example in negative_examples:
                cached_embedding = self.cache_manager.get_embedding(example)
                if cached_embedding is not None:
                    negative_embeddings.append(cached_embedding)
            
            if not positive_embeddings:
                self.logger.warning(f"No cached embeddings found for positive examples for {tool_name}")
                return None
            
            # Convert to numpy arrays
            pos_emb_array = np.array(positive_embeddings)
            
            # Compute centroid of positive examples (normalized)
            positive_centroid = np.mean(pos_emb_array, axis=0)
            positive_centroid_norm = np.linalg.norm(positive_centroid)
            if positive_centroid_norm > 0:
                positive_centroid = positive_centroid / positive_centroid_norm
            else:
                self.logger.error(f"Zero-norm centroid for {tool_name} - indicates invalid embedding data")
                return None
            
            # Compute distances between positive examples and centroid
            distances = []
            zero_norm_count = 0
            for embedding in positive_embeddings:
                emb_array = np.array(embedding)
                norm = np.linalg.norm(emb_array)
                if norm > 0:
                    normalized_embedding = emb_array / norm
                    # Distance = 1 - cosine similarity
                    dist = 1.0 - float(np.dot(normalized_embedding, positive_centroid))
                    distances.append(dist)
                else:
                    zero_norm_count += 1
            
            if zero_norm_count > 0:
                self.logger.warning(f"{zero_norm_count} zero-norm embeddings found for {tool_name}")
                
            if not distances:
                self.logger.error(f"No valid embeddings for {tool_name} - all have zero norm")
                return None
            
            # Calculate threshold adaptively
            if len(distances) >= 10:
                # If we have enough examples, use statistical method
                mean_distance = float(np.mean(distances))
                std_distance = float(np.std(distances))
                # Use mean + 2 standard deviations to cover ~97.5% of positive examples
                threshold = mean_distance + 2.0 * std_distance
            else:
                # With few examples, use a more generous threshold
                if distances:
                    max_distance = max(distances)
                    threshold = max_distance * 1.2  # Allow some margin
                else:
                    threshold = 0.3  # Default fallback
            
            # Verify threshold using negative examples
            if negative_embeddings:
                neg_distances = []
                
                for embedding in negative_embeddings:
                    emb_array = np.array(embedding)
                    norm = np.linalg.norm(emb_array)
                    if norm > 0:
                        normalized_embedding = emb_array / norm
                        dist = 1.0 - float(np.dot(normalized_embedding, positive_centroid))
                        neg_distances.append(dist)
                
                negative_distances = neg_distances
                
                # Check if threshold would incorrectly classify negative examples
                false_positives = sum(1 for d in negative_distances if d <= threshold)
                false_positive_rate = false_positives / len(negative_distances) if negative_distances else 0
                
                # If false positive rate is too high, adjust threshold
                if false_positive_rate > 0.1 and negative_distances:  # More than 10% false positives
                    # Find a better threshold that balances false positives and false negatives
                    all_distances = [(d, True) for d in distances] + [(d, False) for d in negative_distances]
                    all_distances.sort(key=lambda x: x[0])  # Sort by distance
                    
                    best_threshold = threshold
                    best_error = float('inf')
                    
                    # Try different thresholds to find the one with minimum error
                    for i in range(len(all_distances)):
                        candidate_threshold = all_distances[i][0]
                        false_negatives = sum(1 for d, pos in all_distances if pos and d > candidate_threshold)
                        false_positives = sum(1 for d, pos in all_distances if not pos and d <= candidate_threshold)
                        
                        error = false_negatives + false_positives
                        if error < best_error:
                            best_error = error
                            best_threshold = candidate_threshold
                    
                    threshold = best_threshold
            
            # Clamp threshold to reasonable range to prevent extreme values
            threshold = max(0.05, min(float(threshold), 0.6))
            
            # Create classifier data
            classifier_data: Dict[str, Any] = {
                "tool_name": tool_name,
                "positive_centroid": positive_centroid.tolist(),
                "threshold": threshold,
                "positive_count": len(positive_embeddings),
                "negative_count": len(negative_embeddings),
                "timestamp": datetime.now().isoformat()
            }
            
            self.logger.info(f"Created classifier for {tool_name} with threshold {threshold:.4f}")
            return classifier_data
            
        except Exception as e:
            self.logger.error(f"Error creating classifier for {tool_name}: {e}")
            return None
    
    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """
        Compute an embedding for text using the provided model.
        
        Args:
            text: Text to embed
            
        Returns:
            List of embedding values, or None if computation fails
        """
        try:
            if self.model is None:
                self.logger.error("No embedding model provided")
                return None
            
            # Use encode_realtime for tool classification (fast path)
            embedding = self.model.encode_realtime(text)
            return embedding.tolist() if hasattr(embedding, 'tolist') else embedding
            
        except Exception as e:
            self.logger.error(f"Error computing embedding: {e}")
            return None
    
    def precompute_tool_embeddings_matrix(self) -> None:
        """
        Precompute tool embeddings matrix for efficient matrix operations.
        
        This creates a matrix where each row is a tool's positive centroid embedding,
        allowing for fast batch similarity calculations.
        """
        if not self.classifiers:
            self.logger.warning("No classifiers available for precomputing embeddings matrix")
            return
            
        import numpy as np
        
        tool_embeddings = []
        tool_names = []
        expected_dim = 384  # AllMiniLM embeddings should be 384-dimensional
        
        for tool_name, classifier_data in self.classifiers.items():
            positive_centroid = classifier_data["positive_centroid"]
            
            # Validate embedding dimension
            if len(positive_centroid) != expected_dim:
                self.logger.error(f"Tool {tool_name} has embedding dimension {len(positive_centroid)}, expected {expected_dim}. Skipping.")
                continue
                
            tool_embeddings.append(positive_centroid)
            tool_names.append(tool_name)
        
        if not tool_embeddings:
            self.logger.error("No valid tool embeddings found with correct dimensions")
            self.tool_embeddings_matrix = None
            self.tool_names_order = []
            return
        
        # Create matrix (tools x embedding_dim)
        self.tool_embeddings_matrix = np.array(tool_embeddings)
        self.tool_names_order = tool_names
        
        matrix_shape = self.tool_embeddings_matrix.shape
        self.logger.info(f"Precomputed embeddings matrix for {len(tool_names)} tools with shape {matrix_shape}")
    
    def classify_message_with_scores(self, message: EmbeddedMessage) -> List[Tuple[str, float]]:
        """
        Classify a message to determine which tools are relevant, with confidence scores.
        
        This method automatically uses matrix operations if available, otherwise falls back
        to individual classifier evaluation.
        
        Args:
            message: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of (tool_name, confidence_score) tuples where confidence is 0.0-1.0
        """
        # Try matrix operations first if available
        if self.tool_embeddings_matrix is not None and len(self.tool_names_order) > 0:
            try:
                return self._classify_with_matrix_operations(message)
            except Exception as e:
                self.logger.error(f"Matrix classification failed: {e}, falling back to regular classification")
        
        # Fallback to regular classification
        return self._classify_with_individual_classifiers(message)
    
    def _classify_with_matrix_operations(self, message: EmbeddedMessage) -> List[Tuple[str, float]]:
        """
        Classify a message using efficient matrix operations.
        
        PRESERVES: BUG #2 - Division by zero in matrix operations
        
        Args:
            message: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of (tool_name, confidence_score) tuples
        """        
        import numpy as np
        
        try:
            # Get pre-computed message embedding (already normalized)
            message_embedding = message.embedding_384
            
            # Validate dimensions before matrix multiplication
            if self.tool_embeddings_matrix.shape[1] != message_embedding.shape[0]:
                self.logger.error(f"Dimension mismatch: tool matrix has {self.tool_embeddings_matrix.shape[1]} dimensions, message has {message_embedding.shape[0]} dimensions")
                return []
            
            # Batch similarity calculation using matrix multiplication
            # Shape: (n_tools,) = (n_tools, embedding_dim) @ (embedding_dim,)
            # Use np.dot instead of @ to avoid numpy 2.2.6 + Accelerate BLAS warnings
            similarities = np.dot(self.tool_embeddings_matrix, message_embedding)
            
            # Get thresholds for all tools
            thresholds = np.array([
                self.classifiers[tool_name]["threshold"]
                for tool_name in self.tool_names_order
            ])
            
            # Calculate distances and confidence scores
            distances = 1.0 - similarities
            
            # Vectorized confidence calculation
            tool_scores = []
            for i, (tool_name, distance, threshold) in enumerate(
                zip(self.tool_names_order, distances, thresholds)
            ):
                if distance <= threshold:
                    # Safe confidence calculation with zero-threshold protection
                    if threshold > 0:
                        confidence = 1.0 - (distance / (threshold * 2))
                        confidence = max(0.5, min(1.0, confidence))
                    else:
                        # Fallback for zero threshold
                        confidence = 0.5
                    tool_scores.append((tool_name, confidence))
            
            # Sort by confidence score
            tool_scores.sort(key=lambda x: x[1], reverse=True)
            return tool_scores
            
        except Exception as e:
            self.logger.error(f"Error in matrix classification: {e}")
            return []
    
    def _classify_with_individual_classifiers(self, message: EmbeddedMessage) -> List[Tuple[str, float]]:
        """
        Classify using individual classifier evaluation (original method).
        
        PRESERVES: All original classification bugs and logic
        
        Args:
            message: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of (tool_name, confidence_score) tuples
        """
        if not self.classifiers:
            self.logger.error("No classifiers available for classification")
            return []
        
        if self.model is None:
            self.logger.error("Model not initialized, cannot classify message")
            return []
        
        try:
            import numpy as np
            
            # Get pre-computed message embedding
            message_embedding = message.embedding_384.tolist()
            
            # Normalize message embedding
            message_embedding = np.array(message_embedding)
            message_norm = np.linalg.norm(message_embedding)
            if message_norm > 0:
                message_embedding = message_embedding / message_norm
            else:
                self.logger.error("Zero-norm message embedding in individual classification")
                return []
            
            # Evaluate each classifier
            tool_scores = []
            
            for tool_name, classifier_data in self.classifiers.items():
                # Get classifier components
                positive_centroid = np.array(classifier_data["positive_centroid"])
                threshold = classifier_data["threshold"]
                
                # Calculate similarity and distance to positive centroid
                similarity = float(np.dot(message_embedding, positive_centroid))
                distance = 1.0 - similarity
                
                # Calculate confidence score (1.0 = perfect match, 0.0 = not relevant)
                # Normalize based on threshold: confidence is 0.5 at threshold, scaling up to 1.0
                if distance <= threshold:
                    # Convert distance to confidence score (inverted and scaled)
                    # At distance=0, confidence=1.0; at distance=threshold, confidence=0.5
                    if threshold > 0:
                        confidence = 1.0 - (distance / (threshold * 2))
                        confidence = max(0.5, min(1.0, confidence))  # Clamp to [0.5, 1.0]
                    else:
                        # Fallback for zero threshold
                        confidence = 0.5
                    
                    tool_scores.append((tool_name, confidence))
                    self.logger.info(f"Tool {tool_name} is relevant (distance: {distance:.4f}, threshold: {threshold:.4f}, confidence: {confidence:.4f})")
                else:
                    self.logger.debug(f"Tool {tool_name} is not relevant (distance: {distance:.4f}, threshold: {threshold:.4f})")
            
            # Special case: if no tools are relevant but one is very close
            if not tool_scores:
                closest_tool = None
                closest_distance = float('inf')
                closest_threshold = 0.0
                
                for tool_name, classifier_data in self.classifiers.items():
                    positive_centroid = np.array(classifier_data["positive_centroid"])
                    threshold = classifier_data["threshold"]
                    
                    similarity = np.dot(message_embedding, positive_centroid)
                    distance = 1.0 - similarity
                    
                    # Track closest tool
                    if distance < closest_distance:
                        closest_distance = distance
                        closest_tool = tool_name
                        closest_threshold = threshold
                
                # If closest tool is within a relaxed threshold, use it
                relaxed_factor = 1.5
                if closest_tool and closest_distance <= relaxed_factor * closest_threshold:
                    # Confidence score between 0.0 and 0.5
                    if closest_threshold > 0:
                        confidence = 0.5 - ((closest_distance - closest_threshold) / (closest_threshold * relaxed_factor))
                        confidence = max(0.2, min(0.5, confidence))  # Clamp to [0.2, 0.5]
                    else:
                        # Fallback for zero threshold
                        confidence = 0.2
                    
                    tool_scores.append((closest_tool, confidence))
                    self.logger.info(f"No tools met threshold, but {closest_tool} was close (distance: {closest_distance:.4f}, confidence: {confidence:.4f})")
            
            # Sort by confidence score (highest first)
            tool_scores.sort(key=lambda x: x[1], reverse=True)
            return tool_scores
        
        except Exception as e:
            self.logger.error(f"Error classifying message: {e}")
            return []
    
    def classify_message(self, message: str) -> List[str]:
        """
        Classify a message to determine which tools are relevant.
        
        Args:
            message: EmbeddedMessage with pre-computed embeddings
            
        Returns:
            List of tool names deemed relevant to the message
        """
        # Get classification with scores
        results_with_scores = self.classify_message_with_scores(message)
        
        # Extract just the tool names
        return [tool_name for tool_name, _ in results_with_scores]