"""
admin_portal/config.py

Application settings loaded from environment variables via pydantic-settings.

Startup rules:
  - GATEWAY_API_KEY is required. If absent, log an error and exit(1).  (Req 2.9)
  - LOG_LEVEL must be one of DEBUG | INFO | WARNING | ERROR.
    If absent or unrecognised, fall back to INFO and emit a warning.   (Req 10.6, 10.7)
"""

import logging
import sys
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Module-level logger — used only during settings construction.
# The main application re-configures the root logger after settings load.
# ---------------------------------------------------------------------------
_logger = logging.getLogger(__name__)

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Required
    # ------------------------------------------------------------------
    GATEWAY_API_KEY: Optional[str] = None

    # ------------------------------------------------------------------
    # Optional with defaults (Req 10.6 / design.md)
    # ------------------------------------------------------------------
    API_GATEWAY_URL: str = "http://api-gateway:8080"
    AUDIT_STORE_URL: str = "http://audit-store:9200"
    MODEL_REGISTRY_URL: str = "http://model-registry:5000"
    REGISTRY_API_KEY: str = ""
    # Used by the admin-only Ollama pull/sync endpoint (routers/ollama_admin.py)
    # to talk to Ollama directly — distinct from OLLAMA_BASE_URL in
    # inference_adapter/config.py, which is a separate service's own setting.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_PULL_TIMEOUT_SECONDS: int = 900

    # Login sessions (Phase 6 — real password login for both users and admins)
    SESSION_TTL_HOURS: int = 8
    SESSION_COOKIE_NAME: str = "portal_session"
    # POC default password for the seeded admin user — only set if the row's
    # password_hash is still null (never overwrites a real admin-chosen one).
    SEED_ADMIN_PASSWORD: str = "admin123"
    PROMETHEUS_URL: str = "http://prometheus:9090"
    GRAFANA_URL: str = "http://grafana:3000"
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------
    # Users / roles / API keys DB (Phase 1 — Persistent DB + RBAC)
    # ------------------------------------------------------------------
    DATABASE_URL: str = "postgresql://llm_user:llm_pass@localhost:5432/llm_platform"
    # Service-to-service secret — protects GET /portal/keys/resolve from
    # anything other than the API Gateway. Never exposed to the browser.
    ADMIN_PORTAL_INTERNAL_KEY: str = "poc-portal-internal-key"
    # Postgres connect timeout (seconds) — deliberately generous by default,
    # since a real DATABASE_URL may point at a remote host (e.g. a managed
    # Postgres instance on another continent) where a cross-region TCP +
    # TLS handshake can genuinely take a couple of seconds. Lower this in a
    # test environment (where "unreachable" should fail fast) via the env
    # var, not by changing the code default.
    DATABASE_CONNECT_TIMEOUT_SECONDS: int = 5

    # ------------------------------------------------------------------
    # pydantic-settings config
    # ------------------------------------------------------------------
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }

    # ------------------------------------------------------------------
    # Startup validation — runs once when the instance is created
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_startup(self) -> "Settings":
        # --- Req 2.9: GATEWAY_API_KEY is mandatory -----------------------
        if not self.GATEWAY_API_KEY:
            # Ensure the message reaches stdout even before logging is set up
            logging.basicConfig(
                level=logging.ERROR,
                format="%(levelname)s %(name)s %(message)s",
                stream=sys.stdout,
            )
            _logger.error(
                "GATEWAY_API_KEY environment variable is not set. Exiting."
            )
            sys.exit(1)

        # --- Req 10.6 / 10.7: LOG_LEVEL validation -----------------------
        normalised = self.LOG_LEVEL.strip().upper() if self.LOG_LEVEL else ""
        if normalised not in _VALID_LOG_LEVELS:
            # Temporarily configure logging so the warning is visible
            logging.basicConfig(
                level=logging.WARNING,
                format="%(levelname)s %(name)s %(message)s",
                stream=sys.stdout,
            )
            _logger.warning(
                "LOG_LEVEL value %r is not recognised (accepted: DEBUG, INFO, "
                "WARNING, ERROR). Defaulting to INFO.",
                self.LOG_LEVEL,
            )
            # Mutate in-place so the rest of the app sees the corrected value
            object.__setattr__(self, "LOG_LEVEL", "INFO")

        return self


# ---------------------------------------------------------------------------
# Module-level singleton — all other modules import this instance directly.
# ---------------------------------------------------------------------------
settings = Settings()
