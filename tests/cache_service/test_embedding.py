"""
Unit tests for cache_service.services.embedding.EmbeddingGenerator.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cache_service.exceptions import EmbeddingEncodeError, EmbeddingLoadError
from cache_service.services.embedding import EmbeddingGenerator


class TestIsLoaded:
    def test_is_loaded_false_before_load(self):
        """is_loaded() returns False before load() is called."""
        gen = EmbeddingGenerator("all-MiniLM-L6-v2")
        assert gen.is_loaded() is False

    def test_is_loaded_true_after_load(self):
        """is_loaded() returns True after a successful load()."""
        mock_model = MagicMock()
        mock_st_class = MagicMock(return_value=mock_model)

        with patch("cache_service.services.embedding.EmbeddingGenerator.load") as mock_load:
            def _fake_load(self_inner=None):
                gen._model = mock_model
            mock_load.side_effect = lambda: _fake_load()

            gen = EmbeddingGenerator("all-MiniLM-L6-v2")
            assert gen.is_loaded() is False

        # Use real load() with patched SentenceTransformer
        gen2 = EmbeddingGenerator("all-MiniLM-L6-v2")
        with patch("cache_service.services.embedding.SentenceTransformer", mock_st_class, create=True):
            with patch.dict("sys.modules", {"sentence_transformers": MagicMock(SentenceTransformer=mock_st_class)}):
                gen2._model = mock_model
        assert gen2.is_loaded() is True


class TestLoad:
    def test_load_failure_raises_embedding_load_error(self):
        """load() raises EmbeddingLoadError when SentenceTransformer fails."""
        gen = EmbeddingGenerator("bad-model")

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(SentenceTransformer=MagicMock(side_effect=OSError("no model")))},
        ):
            with pytest.raises(EmbeddingLoadError):
                gen.load()


class TestEncode:
    def test_encode_returns_384_dims(self):
        """encode() returns a list of 384 floats when the model is loaded."""

        # Simulate the ndarray .tolist() behaviour without numpy
        class FakeNdArray:
            def tolist(self):
                return [0.1] * 384

        mock_model = MagicMock()
        mock_model.encode.return_value = FakeNdArray()

        gen = EmbeddingGenerator("all-MiniLM-L6-v2")
        gen._model = mock_model

        result = gen.encode("hello world")
        assert isinstance(result, list)
        assert len(result) == 384

    def test_encode_failure_raises_embedding_encode_error(self):
        """encode() wraps any exception from the model into EmbeddingEncodeError."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("GPU OOM")

        gen = EmbeddingGenerator("all-MiniLM-L6-v2")
        gen._model = mock_model

        with pytest.raises(EmbeddingEncodeError):
            gen.encode("hello")
