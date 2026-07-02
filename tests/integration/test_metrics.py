"""
tests/integration/test_metrics.py

Integration tests for the API Gateway Prometheus metrics endpoint.

Covers Requirements 10.1–10.4:
  10.1 — llm_api_gateway_requests_total incremented with correct path and
          status_code labels after a completed 200 request.
  10.2 — llm_api_gateway_errors_total incremented with error_code="401" after
          a 401 response.
  10.3 — GET /metrics returns HTTP 200 with Content-Type: text/plain; version=0.0.4.
  10.4 — llm_api_gateway_requests_total metric name present in /metrics output.

Strategy
--------
Uses ``starlette.testclient.TestClient`` (synchronous) backed by
``api_gateway.main.create_app()``.

Prometheus counters are singletons registered in the global REGISTRY.  To
prevent counter value bleed between tests, the ``reset_api_gateway_metrics``
autouse fixture clears the ``_metrics`` dict on each Counter/Histogram before
and after every test (same pattern as ``reset_prometheus_registry`` for the
Security layer in ``tests/conftest.py``).

A ``get_counter_value`` helper parses Prometheus text exposition format to
extract the current value of a specific label combination.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from api_gateway.config import get_settings
from api_gateway.middleware.rate_limit import RateLimitMiddleware
from api_gateway.schemas.imf import (
    IMFCache,
    IMFDocument,
    IMFGovernance,
    IMFRequest,
    IMFResponse,
    IMFRouting,
    IMFUsage,
    IMFUser,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_imf_response() -> IMFDocument:
    """Build a minimal valid IMFDocument to return from the mocked downstream."""
    rid = str(uuid.uuid4())
    return IMFDocument(
        request_id=rid,
        trace_id=rid,
        span_id="",
        timestamp_utc="2024-01-01T00:00:00Z",
        user=IMFUser(),
        request=IMFRequest(model="llama3", messages=[], stream=False),
        governance=IMFGovernance(),
        routing=IMFRouting(),
        cache=IMFCache(),
        response=IMFResponse(
            content="Hello",
            finish_reason="stop",
            usage=IMFUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        ),
        metadata={},
        extensions={},
    )


def get_counter_value(metrics_text: str, metric_name: str, labels: dict) -> float:
    """Extract a counter value from Prometheus text exposition format.

    Scans lines of the form::

        metric_name{label="value",...} <float>

    Returns 0.0 when the label combination is absent (counter never incremented).

    Args:
        metrics_text: Full text body from ``GET /metrics``.
        metric_name:  The bare metric name, e.g. ``"llm_api_gateway_requests_total"``.
        labels:       Dict of label key→value pairs that must all appear in the line.

    Returns:
        The numeric value on the matched line, or ``0.0`` if not found.
    """
    for line in metrics_text.splitlines():
        if line.startswith("#"):
            continue
        if not (
            line.startswith(metric_name + "{")
            or line.startswith(metric_name + " ")
        ):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.split()[-1])
    return 0.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_api_gateway_metrics():
    """Reset API Gateway Prometheus metric counters between tests.

    Clears the internal ``_metrics`` dict on each Counter and Histogram so
    that label-combination child objects from one test do not bleed into the
    next.  Mirrors the ``reset_prometheus_registry`` pattern already used for
    the Security layer in ``tests/conftest.py``.
    """
    from api_gateway.metrics import ERRORS_TOTAL, LATENCY_SECONDS, REQUESTS_TOTAL

    _objects = [REQUESTS_TOTAL, ERRORS_TOTAL, LATENCY_SECONDS]

    for m in _objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass

    yield

    for m in _objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass


# API key used across all tests
_TEST_API_KEY = "test-metrics-key"


@pytest.fixture
def api_gateway_client(monkeypatch):
    """Synchronous TestClient backed by a fresh ``create_app()`` instance.

    Sets the required env vars, clears ``get_settings`` LRU cache, and clears
    ``RateLimitMiddleware._store`` to prevent bleed between tests.

    Yields:
        A ``starlette.testclient.TestClient`` wrapping the API Gateway app.
    """
    monkeypatch.setenv("GATEWAY_API_KEY", _TEST_API_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", "http://security-layer:8081")

    # Clear the settings LRU cache so the new env vars are picked up
    get_settings.cache_clear()

    # Clear rate-limit state so previous tests don't interfere
    RateLimitMiddleware._store.clear()

    from api_gateway.main import create_app

    app = create_app()

    # ``follow_redirects=True`` is the default for TestClient; set explicitly
    # so that GET /metrics (which Starlette redirects to /metrics/) is followed.
    with TestClient(app, raise_server_exceptions=True, follow_redirects=True) as client:
        yield client

    # Cleanup after test
    get_settings.cache_clear()
    RateLimitMiddleware._store.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_requests_total_incremented_after_200(api_gateway_client: TestClient):
    """llm_api_gateway_requests_total is incremented for a 200 response.

    Validates: Requirements 10.1, 10.3

    Steps:
      1. Mock ``forward_to_security`` to return a valid IMFDocument.
      2. POST /v1/chat/completions with a valid API key and body.
      3. Assert the response is HTTP 200.
      4. GET /metrics and parse the text.
      5. Assert ``llm_api_gateway_requests_total{status_code="200",
         path="/v1/chat/completions"}`` equals 1.0.
      6. Assert ``llm_api_gateway_latency_seconds`` has an observation for
         ``/v1/chat/completions``.
    """
    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=make_imf_response()),
    ):
        resp = api_gateway_client.post(
            "/v1/chat/completions",
            headers={"X-Api-Key": "test-metrics-key"},
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    assert resp.status_code == 200, (
        f"Expected HTTP 200 from chat endpoint, got {resp.status_code}: {resp.text}"
    )

    metrics_resp = api_gateway_client.get(
        "/metrics", headers={"X-Api-Key": _TEST_API_KEY}
    )
    assert metrics_resp.status_code == 200, (
        f"Expected HTTP 200 from /metrics, got {metrics_resp.status_code}"
    )

    metrics_text = metrics_resp.text

    requests_value = get_counter_value(
        metrics_text,
        "llm_api_gateway_requests_total",
        {"status_code": "200", "path": "/v1/chat/completions"},
    )
    assert requests_value == 1.0, (
        f"Expected llm_api_gateway_requests_total{{status_code='200', "
        f"path='/v1/chat/completions'}} == 1.0, got {requests_value}.\n"
        f"Metrics output:\n{metrics_text}"
    )

    # Also verify that a latency observation exists for this path.
    # The histogram _count suffix indicates at least one observation was recorded.
    latency_count_value = get_counter_value(
        metrics_text,
        "llm_api_gateway_latency_seconds_count",
        {"path": "/v1/chat/completions"},
    )
    assert latency_count_value >= 1.0, (
        f"Expected llm_api_gateway_latency_seconds_count{{path='/v1/chat/completions'}} "
        f">= 1.0, got {latency_count_value}."
    )


def test_errors_total_incremented_after_401(api_gateway_client: TestClient):
    """llm_api_gateway_errors_total is incremented with error_code='401' after a 401.

    Validates: Requirements 10.2, 10.3

    Steps:
      1. GET /v1/models WITHOUT an X-Api-Key header (triggers 401).
      2. Assert the response is HTTP 401.
      3. GET /metrics and parse the text.
      4. Assert ``llm_api_gateway_errors_total{error_code="401"}`` >= 1.0.
      5. Assert ``llm_api_gateway_requests_total{status_code="401",
         path="/v1/models"}`` >= 1.0.
    """
    resp = api_gateway_client.get("/v1/models")  # no X-Api-Key header
    assert resp.status_code == 401, (
        f"Expected HTTP 401 without X-Api-Key, got {resp.status_code}: {resp.text}"
    )

    metrics_resp = api_gateway_client.get(
        "/metrics", headers={"X-Api-Key": _TEST_API_KEY}
    )
    assert metrics_resp.status_code == 200, (
        f"Expected HTTP 200 from /metrics, got {metrics_resp.status_code}"
    )

    metrics_text = metrics_resp.text

    errors_value = get_counter_value(
        metrics_text,
        "llm_api_gateway_errors_total",
        {"error_code": "401"},
    )
    assert errors_value >= 1.0, (
        f"Expected llm_api_gateway_errors_total{{error_code='401'}} >= 1.0, "
        f"got {errors_value}.\nMetrics output:\n{metrics_text}"
    )

    requests_value = get_counter_value(
        metrics_text,
        "llm_api_gateway_requests_total",
        {"status_code": "401", "path": "/v1/models"},
    )
    assert requests_value >= 1.0, (
        f"Expected llm_api_gateway_requests_total{{status_code='401', "
        f"path='/v1/models'}} >= 1.0, got {requests_value}.\n"
        f"Metrics output:\n{metrics_text}"
    )


def test_metrics_endpoint_returns_200_with_correct_content_type(
    api_gateway_client: TestClient,
):
    """GET /metrics returns HTTP 200 with Content-Type: text/plain; version=0.0.4.

    Also asserts that the metric ``llm_api_gateway_requests_total`` is present
    in the response body, confirming that the API Gateway metrics are registered
    in the default Prometheus registry.

    Validates: Requirements 10.3, 10.4
    """
    resp = api_gateway_client.get("/metrics", headers={"X-Api-Key": _TEST_API_KEY})

    assert resp.status_code == 200, (
        f"Expected HTTP 200 from GET /metrics, got {resp.status_code}: {resp.text}"
    )

    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type, (
        f"Expected Content-Type to contain 'text/plain', got {content_type!r}"
    )
    assert "version=0.0.4" in content_type, (
        f"Expected Content-Type to contain 'version=0.0.4', got {content_type!r}"
    )

    body = resp.text
    assert "llm_api_gateway_requests_total" in body, (
        "Expected 'llm_api_gateway_requests_total' to appear in /metrics output, "
        f"but it was absent.\nBody (first 2000 chars):\n{body[:2000]}"
    )
