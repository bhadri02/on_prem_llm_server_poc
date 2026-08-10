"""
EmbeddingGenerator — wraps sentence-transformers for CPU-only text encoding.

The model is NOT loaded on instantiation. Call ``load()`` once during application
startup (e.g. inside the FastAPI lifespan context manager) and then use ``encode()``
for all subsequent requests.

Validates: Requirements 5.1, 5.2, 6.1, 1.4, 2.5
"""

from __future__ import annotations

import os

from cache_service.exceptions import EmbeddingEncodeError, EmbeddingLoadError


class EmbeddingGenerator:
    """
    Thin wrapper around ``sentence_transformers.SentenceTransformer``.

    Lifecycle
    ---------
    1. Instantiate with a model name — no I/O occurs.
    2. Call ``load()`` once at startup — downloads / loads the model into memory.
    3. Call ``encode(text)`` for each inference request.

    Args:
        model_name: HuggingFace model identifier, e.g. ``"all-MiniLM-L6-v2"``.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Load the SentenceTransformer model into memory (CPU only).

        Called exactly once during application startup. Stores the loaded model
        on ``self._model``.

        Raises:
            EmbeddingLoadError: If the model fails to load for any reason.
        """
        try:
            # This is an on-prem/air-gapped-capable platform — the embedding
            # model must load from the local HF cache only, never phone home
            # to the Hub. Without this, SentenceTransformer() makes a network
            # call on every startup to check for updates (visible as the
            # "sending unauthenticated requests to the HF Hub" warning), which
            # silently degrades to a slow/hanging startup with no internet.
            # setdefault (not direct assignment) so an operator who explicitly
            # needs to pull a NEW model for the first time can still override
            # by exporting HF_HUB_OFFLINE=0 before starting the service —
            # exactly the same "fetch once with internet, then run offline"
            # pattern already used for `ollama pull` in this repo.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

            from sentence_transformers import SentenceTransformer  # import here to keep startup flexible

            self._model = SentenceTransformer(self.model_name, device="cpu")
        except Exception as exc:
            raise EmbeddingLoadError(
                f"Failed to load embedding model '{self.model_name}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def encode(self, text: str) -> list[float]:
        """
        Encode ``text`` into a 384-dimensional float vector.

        Args:
            text: The input string to embed.

        Returns:
            A 384-element ``list[float]`` (the ``all-MiniLM-L6-v2`` output dimension).

        Raises:
            EmbeddingEncodeError: If encoding fails for any reason.
        """
        try:
            return self._model.encode(text).tolist()
        except Exception as exc:
            raise EmbeddingEncodeError(
                f"Failed to encode text with model '{self.model_name}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        """Return ``True`` if the model has been successfully loaded."""
        return self._model is not None
