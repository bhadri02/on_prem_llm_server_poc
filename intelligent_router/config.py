"""Environment-driven settings for the Intelligent Router (Layer 3).

Values are read from environment variables automatically by pydantic-settings.
The env var name for each field is the uppercase version of the field name
(e.g. ``model_matrix_path`` → ``MODEL_MATRIX_PATH``).

Required fields raise ``ValidationError`` at construction time if absent or
empty, causing the lifespan handler to log and abort startup.
"""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(protected_namespaces=())

    # ------------------------------------------------------------------
    # Required — startup fails at Settings() construction if any is absent
    # ------------------------------------------------------------------
    model_matrix_path: str          # MODEL_MATRIX_PATH
    task_rules_path: str            # TASK_RULES_PATH
    audit_store_url: str            # AUDIT_STORE_URL

    # ------------------------------------------------------------------
    # Optional with defaults
    # ------------------------------------------------------------------
    cache_url: str = "http://cache:8086"                          # CACHE_URL
    inference_adapter_url: str = "http://inference-adapter:8087"  # INFERENCE_ADAPTER_URL
    log_level: str = "INFO"                                        # LOG_LEVEL
    inference_timeout_seconds: int = 120                           # INFERENCE_TIMEOUT_SECONDS
    health_check_timeout_seconds: int = 5                          # HEALTH_CHECK_TIMEOUT_SECONDS
    port: int = 8082                                               # PORT

    # ── Distributed tracing (opt-in, disabled by default for POC) ──────────
    tracing_enabled: bool = False   # TRACING_ENABLED
    otel_endpoint: str = "http://otel-collector:4317"  # OTEL_ENDPOINT


# Module-level singleton — import this directly from other modules:
#   from intelligent_router.config import settings
#
# Guarded so that pytest collection (which imports this module) does not
# abort when the three required env vars are absent in the test environment.
# The lifespan handler in main.py will catch missing vars and sys.exit(1).
try:
    settings = Settings()
except Exception:  # noqa: BLE001
    settings = None  # type: ignore[assignment]
