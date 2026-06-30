"""
Shared pytest fixtures for the Model Registry test suite.

Provides:
  - settings_override: patches STORAGE_PATH and REGISTRY_API_KEY env vars
    and clears the get_settings() lru_cache so every test starts clean.
  - async_client: an httpx.AsyncClient backed by the real FastAPI app with
    the lifespan context manager running (storage loaded, _ready = True).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from model_registry.config import get_settings
from model_registry.main import app, lifespan

# anyio backend registration (required by pytest-anyio / anyio pytest plugin)
pytest_plugins = ("anyio",)

# Fixed API key used across all tests that exercise auth
TEST_KEY = "test-secret-key"


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache on get_settings before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage_path(tmp_path):
    """Return a per-test path for models.json under pytest's tmp_path."""
    return str(tmp_path / "models.json")


@pytest.fixture
def settings_override(storage_path, monkeypatch):
    """
    Monkeypatch STORAGE_PATH and REGISTRY_API_KEY so tests use an isolated
    temporary file and a known API key. Clears lru_cache automatically via
    the clear_settings_cache autouse fixture.
    """
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    monkeypatch.setenv("REGISTRY_API_KEY", TEST_KEY)
    get_settings.cache_clear()
    return storage_path


@pytest.fixture
async def async_client(settings_override):
    """
    Async HTTP client backed by the FastAPI app with lifespan running.

    Starts up the app (loads storage, sets _ready = True) then yields an
    AsyncClient. Tears down the app (sets _ready = False) after the test.

    Requires the anyio pytest backend (registered via pytest_plugins above).
    """
    async with lifespan(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
