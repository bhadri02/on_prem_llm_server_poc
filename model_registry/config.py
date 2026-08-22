"""
Configuration module for the Model Registry.

Defines the Settings class (pydantic-settings BaseSettings) that reads
STORAGE_PATH, LOG_LEVEL, REGISTRY_API_KEY, REGISTRY_ENCRYPTION_KEY, and
ALLOW_UNAUTHENTICATED_REGISTRY from environment variables. Exposes a cached
get_settings() factory used throughout the application.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    storage_path: str = "/data/models.json"
    log_level: str = "INFO"
    registry_api_key: str = ""
    # Fernet key (base64, 32 bytes) used to encrypt provider api_key values at
    # rest in models.json. Empty = stored in plaintext (with a startup warning).
    registry_encryption_key: str = ""
    # Explicit, dangerous opt-out: allows the service to start with no
    # REGISTRY_API_KEY configured (auth disabled). Intended for local/dev use
    # only — production deployments must set REGISTRY_API_KEY instead.
    allow_unauthenticated_registry: bool = False

    model_config = {
        "env_prefix": "",  # reads STORAGE_PATH, LOG_LEVEL, REGISTRY_API_KEY, etc. directly
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
