"""
Unit tests for cache_service.routers.cache.make_cache_key.
"""

from __future__ import annotations

import re

from cache_service.routers.cache import make_cache_key


class TestMakeCacheKey:
    def _msgs(self, *contents):
        return [{"role": "user", "content": c} for c in contents]

    def test_same_inputs_same_key(self):
        """Identical inputs always produce the same key."""
        msgs = self._msgs("What is Python?")
        k1 = make_cache_key(msgs, "llama3", "chat")
        k2 = make_cache_key(msgs, "llama3", "chat")
        assert k1 == k2

    def test_whitespace_normalised(self):
        """Leading/trailing whitespace in message content is stripped."""
        msgs_clean = self._msgs("What is Python?")
        msgs_padded = self._msgs("  What is Python?  ")
        k1 = make_cache_key(msgs_clean, "llama3", "chat")
        k2 = make_cache_key(msgs_padded, "llama3", "chat")
        assert k1 == k2

    def test_different_model_different_key(self):
        """Different model identifiers produce different keys."""
        msgs = self._msgs("Hello")
        k1 = make_cache_key(msgs, "llama3", "chat")
        k2 = make_cache_key(msgs, "mistral", "chat")
        assert k1 != k2

    def test_different_task_type_different_key(self):
        """Different task_type values produce different keys."""
        msgs = self._msgs("Explain recursion")
        k1 = make_cache_key(msgs, "llama3", "chat")
        k2 = make_cache_key(msgs, "llama3", "code")
        assert k1 != k2

    def test_output_is_64_char_hex_string(self):
        """The returned key is a 64-character lowercase hex string (SHA-256)."""
        msgs = self._msgs("test input")
        key = make_cache_key(msgs, "model-x", "chat")
        assert len(key) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", key) is not None
