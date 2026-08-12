"""
Configuration module for the API Gateway (Layer 1).

Defines the Settings class (pydantic-settings BaseSettings) that reads all
values from environment variables. Exposes a cached get_settings() factory
used throughout the application.

Startup validation: if GATEWAY_API_KEY is empty or unset the validator raises
ValueError and the process fails to start.

Validates: Requirements 2.1, 10.1, 10.2, 10.3, 10.4, 12.1
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required — env: GATEWAY_API_KEY; empty string causes startup failure
    gateway_api_key: str

    # Required — env: DOWNSTREAM_SECURITY_URL
    downstream_security_url: str

    # env: LOG_LEVEL — default "INFO"
    log_level: str = "INFO"

    # env: PORT — default 8080
    port: int = 8080

    # env: METRICS_PORT — default 9090
    metrics_port: int = 9090

    # env: RATE_LIMIT_WINDOW_SECONDS — default 60 second window. There is no
    # global request-count setting: every key carries its own rate_limit_rpm
    # (admin_portal, resolved via /portal/keys/resolve) — this window length
    # is the only rate-limit-shaped thing that's actually platform-wide,
    # since "rpm" only means something once "per what span" is fixed.
    rate_limit_window_seconds: int = 60

    # env: REDIS_URL — backs RateLimitMiddleware's per-key counters. Redis
    # (not in-process memory) so the limit is enforced correctly across
    # multiple api_gateway replicas, not per-replica.
    redis_url: str = "redis://redis:6379"

    # env: DOWNSTREAM_TIMEOUT — default 10.0 seconds
    downstream_timeout_seconds: float = 10.0

    # ── Identity resolution (Phase 2 — RBAC + per-user API keys) ────────────
    # env: ADMIN_PORTAL_URL — base URL of the Admin Portal API
    admin_portal_url: str = "http://admin-portal:8084"
    # env: ADMIN_PORTAL_INTERNAL_KEY — shared secret for /portal/keys/resolve
    admin_portal_internal_key: str = "poc-portal-internal-key"
    # env: KEY_CACHE_TTL_SECONDS — in-process resolved-key cache TTL
    key_cache_ttl_seconds: float = 30.0

    # ── Distributed tracing (opt-in, disabled by default for POC) ──────────
    # env: TRACING_ENABLED — set to "true" to enable OTel/Jaeger tracing
    tracing_enabled: bool = False
    # env: OTEL_ENDPOINT — OTLP gRPC endpoint of the OTel Collector
    otel_endpoint: str = "http://otel-collector:4317"

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }

    @field_validator("gateway_api_key")
    @classmethod
    def api_key_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "GATEWAY_API_KEY must be set to a non-empty string. "
                "The API Gateway cannot start without a valid API key."
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
