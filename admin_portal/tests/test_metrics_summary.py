"""
admin_portal/tests/test_metrics_summary.py

Unit tests for GET /portal/metrics/summary.

Test matrix
-----------
1. Happy path — all three Prometheus queries return data; MetricsSummary is
   computed correctly (request_rate, error_rate, cache_hit_rate).
2. Zero denominator — requests=0 causes error_rate=null and
   no cache lookups causes cache_hit_rate=null.
3. Prometheus unreachable — httpx.ConnectError → HTTP 502, upstream="prometheus".
4. Prometheus timeout — httpx.TimeoutException → HTTP 502, upstream="prometheus".
5. Prometheus non-2xx — any 5xx from Prometheus → HTTP 502, upstream="prometheus".
6. Empty Prometheus result — all result arrays are empty →
   request_rate=null, error_rate=null, cache_hit_rate=null.

Strategy
--------
- Set ``GATEWAY_API_KEY=test-key`` via monkeypatch so config loads without
  calling sys.exit(1).
- Override ``PROMETHEUS_URL`` to ``http://prometheus-mock:9090`` so tests
  cannot accidentally hit a real Prometheus.
- Use ``respx`` to mock all outbound httpx calls made by the router.
- Use ``httpx.AsyncClient(app=app, base_url="http://test")`` as the test
  client, matching the pattern in the task spec.
- Each test re-imports ``admin_portal.main.app`` to pick up fresh settings.

Validates: Requirements 8.1, 8.2, 8.3
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Prometheus mock helpers
# ---------------------------------------------------------------------------

_PROM_BASE = "http://prometheus-mock:9090"
_PROM_QUERY_URL = f"{_PROM_BASE}/api/v1/query"


def _prom_result(value: float) -> dict:
    """Build a Prometheus instant query response with a single scalar result."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {},
                    "value": [1700000000.0, str(value)],
                }
            ],
        },
    }


def _prom_empty() -> dict:
    """Build a Prometheus instant query response with no result series."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [],
        },
    }


def _prom_result_labeled(label_value: str, value: float) -> dict:
    """Build a Prometheus response with a single labeled result series."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"result": label_value},
                    "value": [1700000000.0, str(value)],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Set required env vars so admin_portal.config loads without sys.exit."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("PROMETHEUS_URL", _PROM_BASE)


@pytest.fixture
def app(set_env):
    """Return the admin_portal FastAPI app with patched config."""
    # Re-import to pick up monkeypatched env vars via pydantic-settings.
    # Because Settings is a module-level singleton we need to patch the
    # settings object's attribute directly for already-imported modules.
    from admin_portal import config as _cfg
    _cfg.settings.PROMETHEUS_URL = _PROM_BASE

    from admin_portal.db.models import User
    from admin_portal.main import app as _app
    from admin_portal.services.session_auth import AuthContext, get_current_session

    _app.dependency_overrides[get_current_session] = lambda: AuthContext(
        user=User(user_id="test-admin", username="test-admin", status="active"),
        roles=["admin"],
        api_key_raw="test-key",
    )
    return _app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _side_effect_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


def _side_effect_timeout(request):
    raise httpx.TimeoutException("timed out", request=request)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(app):
    """Req 8.1 — All Prometheus queries return data; MetricsSummary is correct.

    Mocked values:
      request_rate query  → 2.5 req/s
      error rate num      → 0.5 errors/s
      cache hits          → 80
      cache total         → 100

    Expected:
      request_rate   = 2.5
      error_rate     = 0.5 / 2.5 = 0.2
      cache_hit_rate = 80 / 100  = 0.8
    """
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        # Prometheus will receive multiple GET requests to the same URL with
        # different ?query= params.  respx matches by URL prefix; use side_effect
        # to return different responses for each call in order.
        call_count = 0
        responses = [
            _prom_result(2.5),    # request_rate query
            _prom_result(0.5),    # error numerator query
            _prom_result_labeled("hit", 80.0),   # cache hits (labeled)
            _prom_result(100.0),  # cache total
        ]

        def _sequenced(request):
            nonlocal call_count
            resp = responses[call_count % len(responses)]
            call_count += 1
            return httpx.Response(200, json=resp)

        mock.get(_PROM_QUERY_URL).mock(side_effect=_sequenced)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["request_rate"] == pytest.approx(2.5)
    assert body["error_rate"] == pytest.approx(0.2)
    assert body["cache_hit_rate"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_zero_denominator(app):
    """Req 8.2 — requests=0 → error_rate=null; no cache lookups → cache_hit_rate=null."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        call_count = 0
        responses = [
            _prom_result(0.0),   # request_rate = 0
            _prom_result(0.0),   # error numerator = 0
            _prom_empty(),       # cache hits — empty
            _prom_empty(),       # cache total — empty
        ]

        def _sequenced(request):
            nonlocal call_count
            resp = responses[call_count % len(responses)]
            call_count += 1
            return httpx.Response(200, json=resp)

        mock.get(_PROM_QUERY_URL).mock(side_effect=_sequenced)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 200
    body = response.json()
    # Req 8.2: division by zero → null
    assert body["error_rate"] is None
    assert body["cache_hit_rate"] is None


@pytest.mark.asyncio
async def test_prometheus_unreachable(app):
    """Req 8.3 — ConnectError → HTTP 502 with upstream='prometheus'."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(side_effect=_side_effect_connect_error)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "upstream_unavailable"
    assert body["upstream"] == "prometheus"


@pytest.mark.asyncio
async def test_prometheus_timeout(app):
    """Req 8.3 — TimeoutException → HTTP 502 with upstream='prometheus'."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(side_effect=_side_effect_timeout)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "upstream_unavailable"
    assert body["upstream"] == "prometheus"


@pytest.mark.asyncio
async def test_prometheus_non_2xx_response(app):
    """Req 8.3 — non-2xx Prometheus response → HTTP 502 with upstream='prometheus'."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(
            return_value=httpx.Response(500, json={"error": "internal server error"})
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "upstream_unavailable"
    assert body["upstream"] == "prometheus"


@pytest.mark.asyncio
async def test_empty_prometheus_result(app):
    """Req 8.1/8.2 — Empty result arrays → all fields are null or 0.

    When Prometheus has no data for any metric yet, ``result`` arrays are
    empty.  The endpoint should return null for all three fields.
    """
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(
            return_value=httpx.Response(200, json=_prom_empty())
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 200
    body = response.json()
    # No data recorded — all fields should be null
    assert body["request_rate"] is None
    assert body["error_rate"] is None
    assert body["cache_hit_rate"] is None


@pytest.mark.asyncio
async def test_response_content_type(app):
    """Response has Content-Type: application/json."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(
            return_value=httpx.Response(200, json=_prom_empty())
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert "application/json" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_error_message_describes_prometheus(app):
    """502 error body message mentions 'prometheus'."""
    with respx.mock(base_url=_PROM_BASE, assert_all_called=False) as mock:
        mock.get(_PROM_QUERY_URL).mock(side_effect=_side_effect_connect_error)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/metrics/summary")

    assert response.status_code == 502
    body = response.json()
    assert "prometheus" in body.get("message", "").lower()
