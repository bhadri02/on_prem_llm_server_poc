"""
Unit tests for inference_adapter.config — Settings validation.

Tests instantiate Settings() directly (never via get_settings()) to
avoid lru_cache interference between tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from inference_adapter.config import Settings, get_settings


# ---------------------------------------------------------------------------
# Autouse fixture: always clear the lru_cache so get_settings() tests
# don't bleed into each other.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

def test_defaults():
    """All documented defaults are applied when no env vars are set."""
    s = Settings(
        _env_file=None,  # suppress any local .env file
    )
    assert s.ollama_base_url == "http://inference-ollama:11434"
    assert s.default_model == "llama3.2:3b"
    assert s.default_max_tokens == 2048
    assert s.max_tokens_limit == 4096
    assert s.default_temperature == 0.7
    assert s.ollama_timeout_seconds == 120
    assert s.log_level == "INFO"
    assert s.port == 8087
    assert s.metrics_port == 9090


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------

def test_env_override(monkeypatch):
    """Each field can be overridden via the corresponding env var."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-ollama:11434")
    monkeypatch.setenv("DEFAULT_MODEL", "llama3.2:3b-override")
    monkeypatch.setenv("DEFAULT_MAX_TOKENS", "1024")
    monkeypatch.setenv("MAX_TOKENS_LIMIT", "2048")
    monkeypatch.setenv("DEFAULT_TEMPERATURE", "0.5")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("METRICS_PORT", "9100")

    s = Settings()
    assert s.ollama_base_url == "http://my-ollama:11434"
    assert s.default_model == "llama3.2:3b-override"
    assert s.default_max_tokens == 1024
    assert s.max_tokens_limit == 2048
    assert s.default_temperature == 0.5
    assert s.ollama_timeout_seconds == 60
    assert s.log_level == "DEBUG"
    assert s.port == 9000
    assert s.metrics_port == 9100


# ---------------------------------------------------------------------------
# port validation
# ---------------------------------------------------------------------------

def test_port_out_of_range_raises_low():
    """PORT=0 (below minimum 1) must raise ValidationError."""
    with pytest.raises(ValidationError):
        Settings(port=0)


def test_port_out_of_range_raises_high():
    """PORT=65536 (above maximum 65535) must raise ValidationError."""
    with pytest.raises(ValidationError):
        Settings(port=65536)


# ---------------------------------------------------------------------------
# ollama_timeout_seconds validation
# ---------------------------------------------------------------------------

def test_timeout_out_of_range_raises_low():
    """OLLAMA_TIMEOUT_SECONDS=0 (below minimum 1) must raise ValidationError."""
    with pytest.raises(ValidationError):
        Settings(ollama_timeout_seconds=0)


def test_timeout_out_of_range_raises_high():
    """OLLAMA_TIMEOUT_SECONDS=601 (above maximum 600) must raise ValidationError."""
    with pytest.raises(ValidationError):
        Settings(ollama_timeout_seconds=601)


# ---------------------------------------------------------------------------
# temperature validation
# ---------------------------------------------------------------------------

def test_invalid_temperature_raises():
    """DEFAULT_TEMPERATURE=2.1 (above maximum 2.0) must raise ValidationError."""
    with pytest.raises(ValidationError):
        Settings(default_temperature=2.1)


def test_temperature_boundary_valid_low():
    """DEFAULT_TEMPERATURE=0.0 is valid (boundary)."""
    s = Settings(default_temperature=0.0)
    assert s.default_temperature == 0.0


def test_temperature_boundary_valid_high():
    """DEFAULT_TEMPERATURE=2.0 is valid (boundary)."""
    s = Settings(default_temperature=2.0)
    assert s.default_temperature == 2.0


# ---------------------------------------------------------------------------
# Cross-field: default_max_tokens > max_tokens_limit
# ---------------------------------------------------------------------------

def test_default_max_tokens_exceeds_limit_raises():
    """default_max_tokens > max_tokens_limit must raise ValidationError."""
    with pytest.raises(ValidationError, match="default_max_tokens"):
        Settings(default_max_tokens=5000, max_tokens_limit=4096)


def test_default_max_tokens_equal_to_limit_is_valid():
    """default_max_tokens == max_tokens_limit is allowed (boundary)."""
    s = Settings(default_max_tokens=4096, max_tokens_limit=4096)
    assert s.default_max_tokens == 4096


# ---------------------------------------------------------------------------
# log_level normalisation
# ---------------------------------------------------------------------------

def test_invalid_log_level_falls_back_to_info():
    """An unrecognised LOG_LEVEL must not raise; it silently falls back to INFO."""
    s = Settings(log_level="NONSENSE")
    assert s.log_level == "INFO"


def test_log_level_case_insensitive_normalised():
    """LOG_LEVEL is normalised to uppercase (e.g., 'debug' → 'DEBUG')."""
    s = Settings(log_level="debug")
    assert s.log_level == "DEBUG"


def test_valid_log_levels_accepted():
    """All five standard log levels are accepted without falling back."""
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        s = Settings(log_level=level)
        assert s.log_level == level
