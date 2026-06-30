"""
Example tests for the GET /health endpoint.

Validates: Requirements 7.1, 7.2, 7.3, 7.4
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

import model_registry.routers.health as health_module
from model_registry.config import get_settings
from model_registry.main import app, lifespan

# anyio backend is registered in conftest.py


# ---------------------------------------------------------------------------
# Helper: client WITHOUT running lifespan (tests pre-startup behaviour)
# ---------------------------------------------------------------------------


async def _raw_client():
    """Return an AsyncClient that does NOT trigger the app lifespan."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# 503 before _ready
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_returns_503_before_ready(settings_override):
    """GET /health returns 503 {"status":"starting"} while _ready is False."""
    original_ready = health_module._ready
    health_module._ready = False

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
    finally:
        health_module._ready = original_ready

    assert response.status_code == 503
    assert response.json() == {"status": "starting"}


# ---------------------------------------------------------------------------
# 200 ok after startup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_returns_200_ok_after_startup(async_client):
    """GET /health returns 200 {"status":"ok","storage":"reachable"} after lifespan."""
    response = await async_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"] == "reachable"


# ---------------------------------------------------------------------------
# 200 degraded when storage file deleted post-startup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_returns_degraded_when_storage_deleted(async_client):
    """GET /health returns 200 {"status":"degraded","storage":"unreachable"}
    when the storage file is deleted after a successful startup."""
    storage_path = app.state.storage._storage_path
    os.remove(storage_path)

    response = await async_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["storage"] == "unreachable"


# ---------------------------------------------------------------------------
# /health does not require X-API-Key
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_does_not_require_api_key(async_client):
    """GET /health must NOT return 401 — no auth required."""
    response = await async_client.get("/health")

    assert response.status_code != 401
