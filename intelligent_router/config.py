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
    audit_api_key: str = ""                                        # AUDIT_API_KEY
    # How often to retry audit events that exhausted post_audit_event's own
    # retries (see audit_client.py's _pending queue).
    audit_flush_interval_seconds: int = 30                         # AUDIT_FLUSH_INTERVAL_SECONDS
    # Optional (not required, unlike model_matrix_path/task_rules_path) so
    # existing deployments/tests that predate Phase 2 (RBAC) keep working
    # unchanged. Startup still fails fast if the file at this path is
    # missing/malformed once the lifespan tries to load it.
    policy_matrix_path: str = "policy_matrix.yaml"                 # POLICY_MATRIX_PATH
    # Used to poll admin_portal for live (role, task_type) permission
    # updates — see services/policy_resolver.py. policy_matrix.yaml above
    # is still loaded at startup as the fail-fast baseline / offline
    # fallback if admin_portal is ever unreachable.
    admin_portal_url: str = "http://admin-portal:8084"             # ADMIN_PORTAL_URL
    admin_portal_internal_key: str = ""                             # ADMIN_PORTAL_INTERNAL_KEY
    policy_cache_ttl_seconds: int = 15                              # POLICY_CACHE_TTL_SECONDS
    # Used to poll model_registry for live "active" models — see
    # services/model_registry_resolver.py. model_matrix.yaml (loaded at
    # startup) is still the source of truth for task_defaults and remains
    # the offline fallback if model_registry is ever unreachable; this only
    # makes newly-registered models routable (by pin or entitlement)
    # without a matrix.yaml edit + Router restart.
    model_registry_url: str = "http://model-registry:5000"          # MODEL_REGISTRY_URL
    model_registry_cache_ttl_seconds: int = 30                     # MODEL_REGISTRY_CACHE_TTL_SECONDS
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
