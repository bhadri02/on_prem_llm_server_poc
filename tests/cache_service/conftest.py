"""
Shared pytest fixtures for the Cache Service test suite.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import httpx
import fakeredis.aioredis

import cache_service.routers.health as health_module
from cache_service.main import app
from cache_service.services.exact_cache import ExactCacheService
from cache_service.services.semantic_cache import SemanticCacheService
from cache_service.config import Settings

try:
    from hypothesis import HealthCheck, settings as h_settings
    h_settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    h_settings.load_profile("ci")
except Exception:
    pass


@pytest_asyncio.fixture
async def fake_redis():
    """Async FakeRedis instance with decode_responses=False."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield redis
    await redis.aclose()


@pytest.fixture
def mock_embedding_generator():
    """
    EmbeddingGenerator-like mock whose encode() returns a deterministic
    384-dimensional unit-normalised float list without loading a real model.
    """
    _unit_val = 1.0 / math.sqrt(384)
    _vec = [_unit_val] * 384

    mock = MagicMock()
    mock.encode.return_value = _vec
    mock.is_loaded.return_value = True
    return mock


@pytest_asyncio.fixture
async def app_client(fake_redis, mock_embedding_generator):
    """
    Async HTTP client backed by the real FastAPI app with a stub lifespan that
    injects fake_redis and mock_embedding_generator into app.state without
    loading real models or connecting to a real Redis instance.

    Manually enters the stub lifespan context to properly initialise app.state
    before any requests are made.
    """
    _settings = Settings(
        redis_url="redis://localhost:6379",
        similarity_threshold=0.90,
        max_semantic_entries=500,
    )

    @asynccontextmanager
    async def stub_lifespan(application):
        # Reset health flags
        health_module._ready = False
        health_module._startup_failure_reason = None

        # Inject fake dependencies
        application.state.redis = fake_redis
        application.state.embedding_generator = mock_embedding_generator
        application.state.exact_cache = ExactCacheService(fake_redis)
        application.state.semantic_cache = SemanticCacheService(fake_redis, _settings)

        # Mark service as ready
        health_module._ready = True

        yield

        # Teardown
        health_module._ready = False

    # Replace the lifespan for the duration of this fixture
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = stub_lifespan

    try:
        # Manually enter the lifespan so app.state is populated
        async with stub_lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                yield client
    finally:
        # Restore original lifespan
        app.router.lifespan_context = original_lifespan
        health_module._ready = False
        health_module._startup_failure_reason = None
