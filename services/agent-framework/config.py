"""
services/agent-framework/config.py

Environment-driven settings for the Agent Framework (Layer 6).

Values are read from environment variables automatically by pydantic-settings.
The env var name for each field is the uppercase version of the field name
(e.g. ``router_url`` → ``ROUTER_URL``).

The lifespan handler in main.py validates required fields and calls sys.exit(1)
on any failure before the HTTP listener starts.
"""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(protected_namespaces=())

    # ------------------------------------------------------------------
    # Required — lifespan validates these are non-empty at startup
    # ------------------------------------------------------------------
    router_url: str = "http://router:8082"          # ROUTER_URL
    gateway_api_key: str = "poc-secret-key"          # GATEWAY_API_KEY
    tool_catalog_path: str = "/config/tools/catalog.yaml"  # TOOL_CATALOG_PATH

    # ------------------------------------------------------------------
    # Optional with defaults
    # ------------------------------------------------------------------
    log_level: str = "INFO"                          # LOG_LEVEL
    max_agent_steps: int = 10                        # MAX_AGENT_STEPS [1, 50]
    port: int = 8083                                 # PORT
    metrics_port: int = 9090                         # METRICS_PORT
    agent_sub_call_timeout_seconds: float = 30.0     # per-LLM-call timeout to Router


# Module-level singleton — import this directly from other modules:
#   from agent_framework.config import settings
#
# Guarded so that pytest collection (which imports this module) does not
# abort when env vars are absent in the test environment.
# The lifespan handler in main.py will catch missing vars and sys.exit(1).
try:
    settings = Settings()
except Exception:  # noqa: BLE001
    settings = None  # type: ignore[assignment]
