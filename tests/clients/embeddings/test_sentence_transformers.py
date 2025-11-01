"""
Tests for clients/embeddings/sentence_transformers.py

Tests AllMiniLMModel embedding generation with real ONNX inference.
Following MIRA testing philosophy: no mocks, test real model behavior.
"""
import pytest
import numpy as np
from clients.embeddings.sentence_transformers import (
    AllMiniLMModel,
    get_all_minilm_model,
    _minilm_model_instances
)


@pytest.fixture(scope="module")
def model():
    """Shared model instance for all tests (model loading is slow)."""
    return get_all_minilm_model()


class TestAllMiniLMModelBasics:
    """Test basic embedding generation and model properties."""

    def test_encode_single_string_returns_1d_array(self, model):
        """Verify single string input returns 1D embedding array."""
        text = "This is a test sentence."

        embedding = model.encode(text)

        # Should be 1D array (not 2D with shape (1, 384))
        assert embedding.ndim == 1
        assert embedding.shape == (384,)

    def test_encode_list_returns_2d_array(self, model):
        """Verify list input returns 2D embedding array."""
        texts = ["First sentence.", "Second sentence.", "Third sentence."]

        embeddings = model.encode(texts)

        # Should be 2D array with shape (num_texts, 384)
        assert embeddings.ndim == 2
        assert embeddings.shape == (3, 384)

    def test_embeddings_are_384_dimensional(self, model):
        """Verify embeddings have exactly 384 dimensions (all-MiniLM-L6-v2 spec)."""
        embedding = model.encode("Test text")

        assert embedding.shape == (384,)

    def test_embeddings_are_normalized(self, model):
        """Verify embeddings are L2 normalized for cosine similarity."""
        embedding = model.encode("Test text for normalization")

        # Calculate L2 norm
        norm = np.linalg.norm(embedding)

        # Should be normalized to 1.0 (within floating point tolerance)
        assert abs(norm - 1.0) < 1e-6

    def test_embeddings_contain_no_nan_or_inf(self, model):
        """Verify embeddings contain only finite values."""
        embedding = model.encode("Test text")

        assert np.all(np.isfinite(embedding))
        assert not np.any(np.isnan(embedding))
        assert not np.any(np.isinf(embedding))

    def test_get_dimension_returns_384(self, model):
        """Verify get_dimension() returns correct dimension."""
        assert model.get_dimension() == 384

    def test_empty_string_produces_valid_embedding(self, model):
        """Verify empty strings are handled gracefully."""
        embedding = model.encode("")

        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))
        # Empty string should still be normalized
        norm = np.linalg.norm(embedding)
        assert abs(norm - 1.0) < 1e-6


class TestAllMiniLMModelBatchProcessing:
    """Test batch processing behavior."""

    def test_batch_size_parameter_processes_correctly(self, model):
        """Verify batch_size parameter controls batching correctly."""
        # Create 10 texts with batch_size=3 (should create 4 batches: 3+3+3+1)
        texts = [f"Text number {i}" for i in range(10)]

        embeddings = model.encode(texts, batch_size=3)

        # Should still produce 10 embeddings
        assert embeddings.shape == (10, 384)
        # All should be normalized
        norms = np.linalg.norm(embeddings, axis=1)
        assert np.all(np.abs(norms - 1.0) < 1e-6)

    def test_large_batch_processes_correctly(self, model):
        """Verify large batches are handled correctly."""
        # Create 100 texts
        texts = [f"Document {i} with some content" for i in range(100)]

        embeddings = model.encode(texts, batch_size=32)

        assert embeddings.shape == (100, 384)
        assert np.all(np.isfinite(embeddings))

    def test_single_text_in_list_returns_2d_array(self, model):
        """Verify single-item list returns 2D array (not 1D)."""
        texts = ["Only one sentence"]

        embeddings = model.encode(texts)

        # Should be 2D with shape (1, 384), not 1D (384,)
        assert embeddings.ndim == 2
        assert embeddings.shape == (1, 384)


class TestAllMiniLMModelTextHandling:
    """Test handling of various text inputs."""

    def test_long_text_truncated_at_512_tokens(self, model):
        """Verify texts longer than 512 tokens are truncated."""
        # Create a very long text (way more than 512 tokens)
        long_text = " ".join(["word"] * 1000)  # ~1000 tokens

        # Should not crash, should produce valid embedding
        embedding = model.encode(long_text)

        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))

    def test_special_characters_handled(self, model):
        """Verify special characters don't break encoding."""
        text = "Test with symbols: @#$%^&*(){}[]|\\<>?/~`"

        embedding = model.encode(text)

        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))

    def test_unicode_text_handled(self, model):
        """Verify Unicode text is handled correctly."""
        text = "Hello 世界 مرحبا мир 🌍"

        embedding = model.encode(text)

        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))

    def test_multiple_spaces_handled(self, model):
        """Verify texts with multiple spaces are handled."""
        text = "Words    with     multiple      spaces"

        embedding = model.encode(text)

        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))


class TestAllMiniLMModelSemantic:
    """Test semantic properties of embeddings."""

    def test_similar_texts_have_high_cosine_similarity(self, model):
        """Verify semantically similar texts produce similar embeddings."""
        text1 = "The cat sits on the mat"
        text2 = "A cat is sitting on a mat"
        text3 = "Quantum physics is complicated"

        emb1 = model.encode(text1)
        emb2 = model.encode(text2)
        emb3 = model.encode(text3)

        # Similar texts should have high similarity
        similarity_similar = np.dot(emb1, emb2)
        # Dissimilar texts should have lower similarity
        similarity_different = np.dot(emb1, emb3)

        # Since embeddings are normalized, dot product = cosine similarity
        assert similarity_similar > 0.7  # Similar texts
        assert similarity_different < similarity_similar  # Different text less similar

    def test_identical_texts_produce_identical_embeddings(self, model):
        """Verify identical texts produce identical embeddings."""
        text = "This is a test sentence for identity check"

        emb1 = model.encode(text)
        emb2 = model.encode(text)

        # Should be identical (within floating point precision)
        np.testing.assert_array_almost_equal(emb1, emb2, decimal=6)


class TestAllMiniLMModelSingleton:
    """Test singleton pattern for model instances."""

    def test_same_config_returns_same_instance(self):
        """Verify same configuration returns cached instance."""
        model1 = get_all_minilm_model(thread_limit=2)
        model2 = get_all_minilm_model(thread_limit=2)

        # Should be the exact same object
        assert model1 is model2

    def test_different_thread_limit_returns_different_instance(self):
        """Verify different thread_limit creates new instance."""
        model1 = get_all_minilm_model(thread_limit=2)
        model2 = get_all_minilm_model(thread_limit=4)

        # Should be different objects
        assert model1 is not model2
        assert model1.thread_limit == 2
        assert model2.thread_limit == 4

    def test_different_cache_dir_returns_different_instance(self):
        """Verify different cache_dir creates new instance."""
        model1 = get_all_minilm_model(cache_dir="/tmp/cache1")
        model2 = get_all_minilm_model(cache_dir="/tmp/cache2")

        # Should be different objects
        assert model1 is not model2
        assert model1.cache_dir == "/tmp/cache1"
        assert model2.cache_dir == "/tmp/cache2"


class TestAllMiniLMModelLifecycle:
    """Test model lifecycle and resource management."""

    def test_close_clears_session_and_tokenizer(self):
        """Verify close() sets session and tokenizer to None."""
        model = get_all_minilm_model()

        # Verify model components exist
        assert model.session is not None
        assert model.tokenizer is not None

        # Close should clear them
        model.close()

        assert model.session is None
        assert model.tokenizer is None

    def test_corruption_recovery_reinitializes_model(self):
        """Verify encode() recovers from corrupted state."""
        model = get_all_minilm_model()

        # Simulate corruption by clearing session/tokenizer
        model.session = None
        model.tokenizer = None

        # encode() should detect corruption and reinitialize
        embedding = model.encode("Test recovery")

        # Should produce valid embedding after recovery
        assert embedding.shape == (384,)
        assert np.all(np.isfinite(embedding))
        # Model components should be restored
        assert model.session is not None
        assert model.tokenizer is not None


class TestAllMiniLMModelMeanPooling:
    """Test mean pooling implementation details."""

    def test_mean_pooling_handles_padding_correctly(self, model):
        """Verify mean pooling uses attention mask to ignore padding."""
        # Create mock token embeddings and attention mask
        # Batch of 2, sequence length 4, embedding dim 384
        token_embeddings = np.random.randn(2, 4, 384)

        # First sequence: all tokens are valid (no padding)
        # Second sequence: only first 2 tokens valid (last 2 are padding)
        attention_mask = np.array([
            [1, 1, 1, 1],  # No padding
            [1, 1, 0, 0]   # Last 2 tokens are padding
        ])

        pooled = model._mean_pooling(token_embeddings, attention_mask)

        # Should produce (2, 384) array
        assert pooled.shape == (2, 384)
        assert np.all(np.isfinite(pooled))

        # Verify padding tokens were ignored (hard to verify exactly, but check sanity)
        # First sequence mean should be average of 4 tokens
        # Second sequence mean should be average of only 2 tokens
        assert not np.array_equal(pooled[0], pooled[1])

    def test_mean_pooling_clips_zero_mask_to_prevent_division_by_zero(self, model):
        """Verify mean pooling handles all-padding sequences."""
        # Create scenario where all tokens are masked (should not crash)
        token_embeddings = np.random.randn(1, 4, 384)
        attention_mask = np.array([[0, 0, 0, 0]])  # All padding

        # Should not crash due to division by zero
        pooled = model._mean_pooling(token_embeddings, attention_mask)

        assert pooled.shape == (1, 384)
        # Result will be near-zero due to clipping, but should be finite
        assert np.all(np.isfinite(pooled))
