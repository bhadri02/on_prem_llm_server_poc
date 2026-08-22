from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    audit_api_key: str  # AUDIT_API_KEY — required; empty = startup failure
    # Postgres connection string (Core Table via SQLAlchemy — see database.py).
    # Same default as admin_portal's DATABASE_URL: one shared Postgres
    # instance/database for this POC, distinguished by table name
    # (audit_events vs. users/roles/api_keys/...), not a separate database.
    database_url: str = "postgresql://llm_user:llm_pass@localhost:5432/llm_platform"  # DATABASE_URL
    log_level: str = "INFO"  # LOG_LEVEL — defaults to INFO if missing/invalid
    # Days of audit_events history to keep; rows older than this are purged
    # by a daily background loop (see main.py's _retention_loop). Defaults
    # to 0 (disabled — keep forever) rather than some positive number, so
    # upgrading an existing deployment never silently starts deleting
    # historical audit/compliance data; operators opt in explicitly.
    retention_days: int = 0  # AUDIT_RETENTION_DAYS
    retention_check_interval_seconds: int = 86400  # AUDIT_RETENTION_CHECK_INTERVAL_SECONDS


settings = Settings()
