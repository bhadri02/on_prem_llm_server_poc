"""
Configuration module for the Model Registry.

Defines the Settings class (pydantic-settings BaseSettings) that reads
STORAGE_PATH, LOG_LEVEL, and REGISTRY_API_KEY from environment variables.
Exposes a cached get_settings() factory used throughout the application.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    storage_path: str = "/data/models.json"
    log_level: str = "INFO"
    registry_api_key: str = ""

    model_config = {
        "env_prefix": "",  # reads STORAGE_PATH, LOG_LEVEL, REGISTRY_API_KEY directly
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
