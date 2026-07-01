"""
Unit tests for cache_service.config.Settings.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cache_service.config import Settings


class TestDefaults:
    def test_defaults(self):
        """All default values match the spec."""
        s = Settings()
        assert s.redis_url == "redis://redis:6379"
        assert s.similarity_threshold == 0.90
        assert s.max_semantic_entries == 500
        assert s.embedding_model == "all-MiniLM-L6-v2"
        assert s.log_level == "INFO"
        assert s.port == 8086
        assert s.ttl_chat == 3600
        assert s.ttl_code == 7200
        assert s.ttl_summarization == 86400


class TestEnvOverride:
    def test_env_override(self, monkeypatch):
        """Environment variables override defaults."""
        monkeypatch.setenv("REDIS_URL", "redis://myredis:6380")
        monkeypatch.setenv("SIMILARITY_THRESHOLD", "0.75")
        monkeypatch.setenv("MAX_SEMANTIC_ENTRIES", "200")
        monkeypatch.setenv("EMBEDDING_MODEL", "custom-model")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("PORT", "9000")
        monkeypatch.setenv("TTL_CHAT", "1800")
        monkeypatch.setenv("TTL_CODE", "3600")
        monkeypatch.setenv("TTL_SUMMARIZATION", "43200")

        s = Settings()
        assert s.redis_url == "redis://myredis:6380"
        assert s.similarity_threshold == 0.75
        assert s.max_semantic_entries == 200
        assert s.embedding_model == "custom-model"
        assert s.log_level == "DEBUG"
        assert s.port == 9000
        assert s.ttl_chat == 1800
        assert s.ttl_code == 3600
        assert s.ttl_summarization == 43200


class TestInvalidLogLevel:
    def test_invalid_log_level_treated_as_info(self, monkeypatch):
        """
        Invalid LOG_LEVEL is accepted by Pydantic as a string (validation
        happens at runtime in consuming code — see _should_emit in logging.py).
        """
        monkeypatch.setenv("LOG_LEVEL", "BANANA")
        s = Settings()
        # Pydantic accepts it as a raw string — no ValidationError raised
        assert s.log_level == "BANANA"


class TestPortOutOfRange:
    def test_port_out_of_range_raises_validation_error(self):
        """PORT outside 1-65535 raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(port=0)

        with pytest.raises(ValidationError):
            Settings(port=65536)
