"""
Configuration module for the Cache Service (Layer 4).

Defines the Settings class (pydantic-settings BaseSettings) that reads all
values from environment variables with no hardcoded values. Exposes a cached
get_settings() factory used throughout the application.

Validates: Requirements 6.9, 4.2, 4.4, 5.5, 5.6, 6.8
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Redis connection — env: REDIS_URL
    # Default matches the standalone Redis service name used in local/POC Helm deploy
    # (redisStandalone.enabled=true → service: llm-poc-cache-redis).
    # For Bitnami sub-chart deploys the service is llm-poc-cache-redis-master —
    # always override via REDIS_URL env var in the Helm values.
    redis_url: str = "redis://llm-poc-cache-redis:6379"

    # Semantic cache threshold — env: SIMILARITY_THRESHOLD; float in [0.0, 1.0]
    similarity_threshold: float = Field(0.90, ge=0.0, le=1.0)

    # Maximum semantic entries per task_type — env: MAX_SEMANTIC_ENTRIES; positive int
    max_semantic_entries: int = Field(500, gt=0)

    # Sentence-transformer model name — env: EMBEDDING_MODEL
    embedding_model: str = "all-MiniLM-L6-v2"

    # Structured log level — env: LOG_LEVEL; invalid values normalised to "INFO" in consuming code
    log_level: str = "INFO"

    # Application port — env: PORT; int in [1, 65535]; ValidationError causes startup failure
    port: int = Field(8086, ge=1, le=65535)

    # Prometheus metrics port — env: METRICS_PORT; defaults to 9091 to avoid collision with inference_adapter (9090)
    metrics_port: int = Field(9091, ge=1, le=65535)

    # Cache TTL (seconds) — env: CACHE_TTL_SECONDS. Applies uniformly to both
    # the exact-match cache (native Redis key expiry) and the semantic cache
    # (age-checked at lookup time, since it's stored as a shared Redis List
    # rather than one key per entry) — same TTL, same behavior, every
    # task_type. A cache hit older than this is treated as a miss.
    cache_ttl_seconds: int = Field(60, gt=0)

    # ── Distributed tracing (opt-in, disabled by default for POC) ──────────
    tracing_enabled: bool = False   # TRACING_ENABLED
    otel_endpoint: str = "http://otel-collector:4317"  # OTEL_ENDPOINT

    model_config = {
        "env_prefix": "",       # reads REDIS_URL, SIMILARITY_THRESHOLD, etc. directly
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
