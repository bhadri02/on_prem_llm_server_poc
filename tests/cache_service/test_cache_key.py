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

    def test_only_last_message_used_not_full_history(self):
        """Real bug regression: the key must be derived from the CURRENT
        turn only, not the whole conversation. The Chat UI resends full
        history every turn (stateless backend) — hashing all of it would
        make the key dominated by the ever-growing shared prefix instead of
        the actual new question, causing unrelated questions late in a
        conversation to collide (observed live: "do you know the time"
        semantically matched a cached "good morning" reply)."""
        single_turn = self._msgs("do you know the time")
        multi_turn_same_question = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "How can I assist you today?"},
            {"role": "user", "content": "good morning"},
            {"role": "assistant", "content": "Good morning! How can I help?"},
            {"role": "user", "content": "do you know the time"},
        ]

        k_single = make_cache_key(single_turn, "llama3", "chat")
        k_multi = make_cache_key(multi_turn_same_question, "llama3", "chat")

        assert k_single == k_multi

    def test_different_final_question_different_key_regardless_of_shared_history(self):
        """Two conversations sharing an identical long history but ending in
        a different question must NOT collide."""
        shared_prefix = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "How can I assist you today?"},
            {"role": "user", "content": "good morning"},
            {"role": "assistant", "content": "Good morning! How can I help?"},
        ]
        convo_a = shared_prefix + [{"role": "user", "content": "do you know the time"}]
        convo_b = shared_prefix + [{"role": "user", "content": "what is the capital of France"}]

        assert make_cache_key(convo_a, "llama3", "chat") != make_cache_key(convo_b, "llama3", "chat")
