"""
Unit tests for GET /health endpoint.
Uses the app_client fixture (stub lifespan, fake redis).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

import cache_service.routers.health as health_module


class TestHealthStarting:
    async def test_starting_returns_503(self, app_client):
        """
        Returns 503 {"status": "starting"} when _ready is False.
        We temporarily override the module flag after the fixture sets _ready=True.
        """
        original = health_module._ready
        try:
            health_module._ready = False
            resp = await app_client.get("/health")
            assert resp.status_code == 503
            assert resp.json()["status"] == "starting"
        finally:
            health_module._ready = original


class TestHealthReady:
    async def test_ready_redis_ok_returns_200(self, app_client):
        """Returns 200 {"status": "ok"} when ready and Redis responds to ping."""
        # The app_client fixture already sets _ready=True and uses fake_redis which pings fine
        resp = await app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_ready_redis_unreachable_returns_503(self, app_client, fake_redis):
        """Returns 503 {"status":"unavailable","reason":"redis_unreachable"} when Redis ping fails."""
        with patch.object(fake_redis, "ping", new=AsyncMock(side_effect=Exception("ping failed"))):
            resp = await app_client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unavailable"
        assert body["reason"] == "redis_unreachable"

    async def test_embedding_load_failed_returns_503(self, app_client):
        """Returns 503 with reason=embedding_model_load_failed when startup failure set."""
        original = health_module._startup_failure_reason
        try:
            health_module._startup_failure_reason = "embedding_model_load_failed"
            resp = await app_client.get("/health")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "unavailable"
            assert body["reason"] == "embedding_model_load_failed"
        finally:
            health_module._startup_failure_reason = original
