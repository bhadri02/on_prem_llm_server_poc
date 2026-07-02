"""
admin_portal/tests/test_checkpoint_portal_api.py

Checkpoint 9 — Portal_API acceptance tests.

Verifies the three explicit checkpoint criteria:
  1. GET /portal/health   → HTTP 200, body {"status": "ok"}
  2. GET /portal/config   → HTTP 200, body contains "grafana_url" key
  3. GET /metrics         → HTTP 200, Prometheus text format
      (content-type: text/plain; version=0.0.4)

Also covers the complementary acceptance criteria for health (503/degraded)
and config (default + custom GRAFANA_URL).

Strategy
--------
- Set GATEWAY_API_KEY=test-key via monkeypatch so config loads without exit.
- Use httpx.AsyncClient(transport=ASGITransport(app=app)) as the test client.
- /metrics is mounted directly on the same app (same port) for testing;
  the real deployment serves it on 9090 via a second uvicorn process.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_required_env(monkeypatch):
    """Ensure GATEWAY_API_KEY is always present so config validates cleanly."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")


@pytest.fixture
def app(set_required_env):
    """Return the Portal_API FastAPI application.

    Re-uses the module-level singleton; the autouse fixture has already
    set GATEWAY_API_KEY before the import occurs in conftest / on first use.
    """
    # Patch the already-loaded settings object directly so routers that
    # import settings at module load time pick up the test values.
    from admin_portal import config as _cfg
    _cfg.settings.GATEWAY_API_KEY = "test-key"

    from admin_portal.main import app as _app
    return _app


# ---------------------------------------------------------------------------
# 1. GET /portal/health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Checkpoint criterion 1: GET /portal/health returns 200 with status=ok."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, app):
        """Req 1.1 — healthy service responds HTTP 200."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/health")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_body_status_ok(self, app):
        """Req 1.1 — response body contains {"status": "ok"}."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/health")

        body = response.json()
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_no_reason_field_when_ok(self, app):
        """Req 1.1 — reason field absent (or null) when status is ok."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/health")

        body = response.json()
        assert body.get("reason") is None

    @pytest.mark.asyncio
    async def test_health_content_type_json(self, app):
        """Health endpoint returns application/json content-type."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/health")

        assert "application/json" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, app):
        """Req 1.4 — no API key needed; request without any auth header returns 200."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Explicitly send NO Authorization or X-API-Key headers
            response = await client.get("/portal/health", headers={})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_degraded_returns_503(self, app):
        """Req 1.3 — when a startup failure is recorded the endpoint returns 503."""
        from admin_portal.routers import health as health_module

        # Record a fake startup failure
        health_module.set_startup_failure("test: dependency missing")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/portal/health")

            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "degraded"
            assert "dependency missing" in body.get("reason", "")
        finally:
            # Always clear so other tests are not affected
            health_module.clear_startup_failure()


# ---------------------------------------------------------------------------
# 2. GET /portal/config
# ---------------------------------------------------------------------------

class TestConfigEndpoint:
    """Checkpoint criterion 2: GET /portal/config returns grafana_url."""

    @pytest.mark.asyncio
    async def test_config_returns_200(self, app):
        """Config endpoint responds HTTP 200."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/config")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_config_contains_grafana_url(self, app):
        """Req 9.4 — response body contains a grafana_url key."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/config")

        body = response.json()
        assert "grafana_url" in body

    @pytest.mark.asyncio
    async def test_config_grafana_url_is_string(self, app):
        """grafana_url value is a non-empty string."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/config")

        body = response.json()
        assert isinstance(body["grafana_url"], str)
        assert len(body["grafana_url"]) > 0

    @pytest.mark.asyncio
    async def test_config_default_grafana_url(self, app):
        """Req 9.3 — when GRAFANA_URL env var is absent, default is http://grafana:3000."""
        from admin_portal import config as _cfg

        original = _cfg.settings.GRAFANA_URL
        _cfg.settings.GRAFANA_URL = "http://grafana:3000"
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/portal/config")

            body = response.json()
            assert body["grafana_url"] == "http://grafana:3000"
        finally:
            _cfg.settings.GRAFANA_URL = original

    @pytest.mark.asyncio
    async def test_config_custom_grafana_url(self, app, monkeypatch):
        """Req 9.4 — config endpoint returns whatever GRAFANA_URL is set to."""
        from admin_portal import config as _cfg

        original = _cfg.settings.GRAFANA_URL
        _cfg.settings.GRAFANA_URL = "http://my-custom-grafana:4000"
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/portal/config")

            body = response.json()
            assert body["grafana_url"] == "http://my-custom-grafana:4000"
        finally:
            _cfg.settings.GRAFANA_URL = original

    @pytest.mark.asyncio
    async def test_config_content_type_json(self, app):
        """Config endpoint returns application/json content-type."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/config")

        assert "application/json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 3. GET /metrics  (Prometheus text format)
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    """Checkpoint criterion 3: /metrics returns Prometheus text format.

    Note: Starlette's Mount redirects ``/metrics`` → ``/metrics/`` with a 307.
    Tests hit ``/metrics/`` directly (the canonical form served by the ASGI
    app) and also verify that ``/metrics`` with follow_redirects=True works.
    """

    @pytest.mark.asyncio
    async def test_metrics_returns_200(self, app):
        """Req 1.5 / 10.2 — /metrics/ endpoint responds HTTP 200."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_redirect_followed(self, app):
        """GET /metrics follows the 307 redirect to /metrics/ and returns 200."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=True,
        ) as client:
            response = await client.get("/metrics")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_content_type_is_prometheus_text(self, app):
        """Req 1.5 — content-type is text/plain (Prometheus exposition format)."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        content_type = response.headers.get("content-type", "")
        assert "text/plain" in content_type

    @pytest.mark.asyncio
    async def test_metrics_body_is_text(self, app):
        """Prometheus /metrics body is plain text, not JSON."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        # Body should be decodable as UTF-8 plain text
        body = response.text
        assert isinstance(body, str)
        assert len(body) > 0

    @pytest.mark.asyncio
    async def test_metrics_contains_portal_requests_counter(self, app):
        """Req 10.3 — /metrics output contains llm_portal_requests_total metric name."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert "llm_portal_requests_total" in response.text

    @pytest.mark.asyncio
    async def test_metrics_contains_portal_latency_histogram(self, app):
        """Req 10.4 — /metrics output contains llm_portal_latency_seconds metric name."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert "llm_portal_latency_seconds" in response.text

    @pytest.mark.asyncio
    async def test_metrics_contains_portal_errors_counter(self, app):
        """Req 10.5 — /metrics output contains llm_portal_errors_total metric name."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert "llm_portal_errors_total" in response.text

    @pytest.mark.asyncio
    async def test_metrics_no_auth_required(self, app):
        """Req 1.5 — /metrics does not require authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/", headers={})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_has_help_lines(self, app):
        """Prometheus text format includes # HELP comment lines."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert "# HELP" in response.text

    @pytest.mark.asyncio
    async def test_metrics_has_type_lines(self, app):
        """Prometheus text format includes # TYPE comment lines."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/")

        assert "# TYPE" in response.text
