from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    audit_api_key: str  # AUDIT_API_KEY — required; empty = startup failure
    db_path: str = "/data/audit.db"  # DB_PATH
    log_level: str = "INFO"  # LOG_LEVEL — defaults to INFO if missing/invalid


settings = Settings()
