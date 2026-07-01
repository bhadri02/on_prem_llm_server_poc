"""Environment-driven settings for the Security & Governance Layer.

Values are read from environment variables automatically by pydantic-settings.
The env var name for each field is the uppercase version of the field name
(e.g. ``downstream_router_url`` → ``DOWNSTREAM_ROUTER_URL``).

``pii_enabled`` accepts only ``"true"`` or ``"false"`` (case-insensitive);
any other value raises ``ValidationError`` so the lifespan handler can log
and abort startup.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Required — startup fails at Settings() construction if any is absent
    # ------------------------------------------------------------------
    downstream_router_url: str      # DOWNSTREAM_ROUTER_URL
    audit_store_url: str            # AUDIT_STORE_URL
    audit_api_key: str              # AUDIT_API_KEY
    injection_patterns_path: str    # INJECTION_PATTERNS_PATH

    # ------------------------------------------------------------------
    # Optional with defaults
    # ------------------------------------------------------------------
    log_level: str = "INFO"         # LOG_LEVEL
    pii_enabled: bool = True        # PII_ENABLED (default true)

    @field_validator("pii_enabled", mode="before")
    @classmethod
    def validate_pii_enabled(cls, v: object) -> object:
        if isinstance(v, str) and v.lower() not in ("true", "false"):
            raise ValueError(
                f"PII_ENABLED must be 'true' or 'false' (case-insensitive), got: {v!r}"
            )
        return v


# Module-level singleton — import this directly from other modules:
#   from security_layer.config import settings
settings = Settings()
