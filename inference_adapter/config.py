"""
Configuration module for the Inference Adapter (Layer 5).

Defines the Settings class (pydantic-settings BaseSettings) that reads all
values from environment variables with no hardcoded values. Exposes a cached
get_settings() factory used throughout the application.

Validates: Requirements 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 13.4, 13.5
"""

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    # Ollama backend URL — env: OLLAMA_BASE_URL
    ollama_base_url: str = "http://inference-ollama:11434"

    # Default model to use — env: DEFAULT_MODEL
    default_model: str = "llama3.2:3b"

    # Default max tokens per request — env: DEFAULT_MAX_TOKENS; must be <= max_tokens_limit
    default_max_tokens: int = Field(2048, gt=0)

    # Hard upper limit on tokens — env: MAX_TOKENS_LIMIT; positive int
    max_tokens_limit: int = Field(4096, gt=0)

    # Default sampling temperature — env: DEFAULT_TEMPERATURE; float in [0.0, 2.0]
    default_temperature: float = Field(0.7, ge=0.0, le=2.0)

    # Ollama HTTP timeout in seconds — env: OLLAMA_TIMEOUT_SECONDS; int in [1, 600]
    ollama_timeout_seconds: int = Field(120, ge=1, le=600)

    # Structured log level — env: LOG_LEVEL; invalid values silently fall back to "INFO"
    log_level: str = "INFO"

    # Application port — env: PORT; int in [1, 65535]; ValidationError causes startup failure
    port: int = Field(8087, ge=1, le=65535)

    # Prometheus metrics port — env: METRICS_PORT; int in [1, 65535]; ValidationError causes startup failure
    metrics_port: int = Field(9090, ge=1, le=65535)

    # ── Distributed tracing (opt-in, disabled by default for POC) ──────────
    tracing_enabled: bool = False   # TRACING_ENABLED
    otel_endpoint: str = "http://otel-collector:4317"  # OTEL_ENDPOINT

    model_config = {
        "env_prefix": "",       # reads OLLAMA_BASE_URL, DEFAULT_MODEL, etc. directly
        "case_sensitive": False,
    }

    @field_validator("ollama_base_url", "default_model")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, v: Any) -> str:
        """Silently fall back to INFO for unrecognised log level values."""
        normalised = str(v).upper()
        if normalised not in _VALID_LOG_LEVELS:
            return "INFO"
        return normalised

    @model_validator(mode="after")
    def check_default_max_tokens_le_limit(self) -> "Settings":
        """Cross-field: default_max_tokens must not exceed max_tokens_limit."""
        if self.default_max_tokens > self.max_tokens_limit:
            raise ValueError(
                f"default_max_tokens ({self.default_max_tokens}) must be "
                f"<= max_tokens_limit ({self.max_tokens_limit})"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
