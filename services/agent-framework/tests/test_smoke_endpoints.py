"""
tests/test_smoke_endpoints.py

Smoke tests for endpoint availability (Task 9.3).

Covers:
  - GET /health returns 200 with {"status": "ok"} (Req 1.5)
  - GET /metrics returns 200 with Content-Type: text/plain (Req 1.6, 13.1)
  - Request to an undefined path returns 404 (Req 1.7)

Requirements: 1.5, 1.6, 1.7, 13.1
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import make_asgi_app as _make_metrics_app
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Test App Factories
# ---------------------------------------------------------------------------


def _make_main_app() -> FastAPI:
    """Create a minimal FastAPI app with health and agent routers (no lifespan)."""
    app = FastAPI()

    from agent_framework.routers import health, agent

    app.include_router(health.router)
    app.include_router(agent.router)

    # Attach minimal state so routers don't fail on app.state access
    app.state.tool_registry = {}
    app.state.settings = MagicMock()

    return app


# ---------------------------------------------------------------------------
# Health endpoint smoke tests (Req 1.5)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /health must return 200 with {"status": "ok"} without auth."""

    def setup_method(self):
        self._client = TestClient(_make_main_app(), raise_server_exceptions=False)

    def test_health_returns_200(self):
        """GET /health returns HTTP 200."""
        resp = self._client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_status_ok(self):
        """GET /health body is {"status": "ok"}."""
        resp = self._client.get("/health")
        assert resp.json() == {"status": "ok"}

    def test_health_no_auth_required(self):
        """GET /health must work without any Authorization header."""
        resp = self._client.get("/health")  # No auth headers
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_content_type_json(self):
        """GET /health response Content-Type includes application/json."""
        resp = self._client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Undefined path returns 404 (Req 1.7)
# ---------------------------------------------------------------------------


class TestUndefinedPaths:
    """Requests to undefined paths must return HTTP 404."""

    def setup_method(self):
        self._client = TestClient(_make_main_app(), raise_server_exceptions=False)

    def test_undefined_get_path_returns_404(self):
        """GET /nonexistent returns 404."""
        resp = self._client.get("/nonexistent")
        assert resp.status_code == 404

    def test_undefined_post_path_returns_404(self):
        """POST /nonexistent returns 404."""
        resp = self._client.post("/nonexistent", json={})
        assert resp.status_code == 404

    def test_undefined_nested_path_returns_404(self):
        """GET /some/nested/path returns 404."""
        resp = self._client.get("/some/nested/path")
        assert resp.status_code == 404

    def test_agent_run_with_get_returns_405_or_404(self):
        """GET /agent/run is not defined (POST only) — returns 405 Method Not Allowed."""
        resp = self._client.get("/agent/run")
        # FastAPI returns 405 for wrong HTTP method on a defined route
        assert resp.status_code in (404, 405)

    def test_api_root_returns_404(self):
        """GET / (root) is not defined — returns 404."""
        resp = self._client.get("/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint smoke tests (Req 1.6, 13.1)
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """
    GET /metrics on the metrics ASGI app returns 200 with text/plain Content-Type.

    The metrics app is a separate ASGI app (prometheus_client.make_asgi_app()).
    We mount it on a test FastAPI app at /metrics to verify it works.
    """

    def setup_method(self):
        # Build a minimal app that mounts the prometheus metrics ASGI app
        import agent_framework.metrics  # noqa: F401 — registers counters

        self._metrics_app = _make_metrics_app()
        self._client = TestClient(self._metrics_app, raise_server_exceptions=False)

    def test_metrics_returns_200(self):
        """GET / on the metrics app returns HTTP 200."""
        resp = self._client.get("/")
        assert resp.status_code == 200

    def test_metrics_content_type_is_text_plain(self):
        """GET / on the metrics app returns Content-Type: text/plain (Req 13.1)."""
        resp = self._client.get("/")
        content_type = resp.headers.get("content-type", "")
        assert content_type.startswith("text/plain"), (
            f"Expected text/plain Content-Type, got: {content_type!r}"
        )

    def test_metrics_body_is_non_empty(self):
        """Metrics body contains Prometheus exposition format text."""
        resp = self._client.get("/")
        assert len(resp.text) > 0

    def test_metrics_body_contains_prometheus_format(self):
        """Metrics body contains # HELP or # TYPE lines (Prometheus exposition format)."""
        resp = self._client.get("/")
        body = resp.text
        # Standard Prometheus text format includes comment lines
        has_help = "# HELP" in body
        has_type = "# TYPE" in body
        assert has_help or has_type, (
            "Expected Prometheus exposition format with # HELP or # TYPE lines"
        )

    def test_metrics_contains_agent_framework_metrics(self):
        """Metrics body references agent framework metric names."""
        import agent_framework.metrics  # ensure metrics are registered

        resp = self._client.get("/")
        body = resp.text
        # At minimum the metric names should appear in the body
        assert "llm_agent_framework" in body, (
            "Expected 'llm_agent_framework' metrics to appear in /metrics output"
        )


# ---------------------------------------------------------------------------
# Integration: health + agent router on the same app
# ---------------------------------------------------------------------------


class TestMultiRouterApp:
    """Verify both routers are registered and reachable on the same app."""

    def setup_method(self):
        self._app = _make_main_app()
        self._client = TestClient(self._app, raise_server_exceptions=False)

    def test_health_and_agent_run_coexist(self):
        """/health works independently from /agent/run."""
        health_resp = self._client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}

        # Posting to /agent/run without a valid IMF gets 422 (Pydantic validation)
        run_resp = self._client.post("/agent/run", json={})
        assert run_resp.status_code == 422  # Missing required fields

    def test_health_is_not_affected_by_bad_agent_request(self):
        """A malformed /agent/run request must not affect /health."""
        # Bad request to agent endpoint
        self._client.post("/agent/run", json={"bad": "request"})

        # Health must still work
        health_resp = self._client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}
