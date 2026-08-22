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
    # How often to retry audit events that exhausted post_audit_event's own
    # retries (see audit_client.py's _pending queue).
    audit_flush_interval_seconds: int = 30  # AUDIT_FLUSH_INTERVAL_SECONDS
    pii_enabled: bool = True        # PII_ENABLED (default true)
    # Comma-separated Presidio entity types to scan for. Broadened past the
    # original 3-entity POC default (EMAIL_ADDRESS/PHONE_NUMBER/PERSON only —
    # a real compliance gap for anything handling SSNs, card numbers, etc.);
    # override via PII_ENTITIES for a narrower or jurisdiction-specific set.
    # See Presidio's predefined recognizers for the full supported list.
    pii_entities: str = (
        "EMAIL_ADDRESS,PHONE_NUMBER,PERSON,CREDIT_CARD,US_SSN,IBAN_CODE,"
        "IP_ADDRESS,LOCATION,US_BANK_NUMBER,US_PASSPORT,US_DRIVER_LICENSE"
    )  # PII_ENTITIES
    # Read/write timeout (seconds) for the call to intelligent_router — this
    # budget covers the ENTIRE downstream chain (Router + cache lookup +
    # inference dispatch), not just the Router's own processing. CPU-only
    # Ollama inference can genuinely take 15-20s+ per response even on a
    # single small model; raised well above the old hardcoded 30s default
    # after real 502s were observed with two models loaded locally.
    router_timeout_seconds: float = 120.0   # ROUTER_TIMEOUT_SECONDS

    # ── Distributed tracing (opt-in, disabled by default for POC) ──────────
    tracing_enabled: bool = False   # TRACING_ENABLED
    otel_endpoint: str = "http://otel-collector:4317"  # OTEL_ENDPOINT

    @field_validator("pii_enabled", mode="before")
    @classmethod
    def validate_pii_enabled(cls, v: object) -> object:
        if isinstance(v, str) and v.lower() not in ("true", "false"):
            raise ValueError(
                f"PII_ENABLED must be 'true' or 'false' (case-insensitive), got: {v!r}"
            )
        return v

    @property
    def pii_entities_list(self) -> list[str]:
        """Parsed, upper-cased PII_ENTITIES, e.g. ['EMAIL_ADDRESS', 'PERSON']."""
        return [e.strip().upper() for e in self.pii_entities.split(",") if e.strip()]


# Module-level singleton — import this directly from other modules:
#   from security_layer.config import settings
settings = Settings()
