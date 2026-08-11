"""
admin_portal/tests/test_governance_summary.py

Unit tests for GET /portal/governance/summary — the Audit-Store-backed
proxy that complements the Prometheus-backed GET /portal/metrics/summary.

Test matrix
-----------
1. Happy path — Audit Store returns a full summary; proxied through unchanged.
2. Audit Store unreachable (ConnectError) → HTTP 502, upstream="audit-store".
3. Audit Store timeout → HTTP 502, upstream="audit-store".
4. Audit Store 422 (bad from/to) → relayed through unchanged, not reshaped.
5. from/to query params are forwarded to the upstream URL.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

_AUDIT_BASE = "http://audit-store-mock:9200"
_AUDIT_URL = f"{_AUDIT_BASE}/audit/governance/summary"


def _summary_body() -> dict:
    return {
        "total_events": 10,
        "by_outcome": {"pass": 8, "block": 2},
        "by_layer": {"security": 5, "router": 5},
        "requests_blocked_total": 2,
        "blocked_by_reason": {"injection_detected": 1, "policy_denied": 1},
        "injection_flagged_total": 1,
        "pii_detections_total": 3,
        "token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "model_usage": {"llama3.2:3b": 4},
    }


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("AUDIT_STORE_URL", _AUDIT_BASE)


@pytest.fixture
def app(set_env):
    from admin_portal import config as _cfg
    _cfg.settings.AUDIT_STORE_URL = _AUDIT_BASE

    from admin_portal.db.models import User
    from admin_portal.main import app as _app
    from admin_portal.services.session_auth import AuthContext, get_current_session

    _app.dependency_overrides[get_current_session] = lambda: AuthContext(
        user=User(user_id="test-admin", username="test-admin", status="active"),
        roles=["admin"],
        api_key_raw="test-key",
    )
    return _app


@pytest.mark.asyncio
async def test_happy_path(app):
    with respx.mock(base_url=_AUDIT_BASE, assert_all_called=False) as mock:
        mock.get("/audit/governance/summary").mock(
            return_value=httpx.Response(200, json=_summary_body())
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/governance/summary")

    assert response.status_code == 200
    assert response.json() == _summary_body()


@pytest.mark.asyncio
async def test_audit_store_unreachable(app):
    def _connect_error(request):
        raise httpx.ConnectError("connection refused", request=request)

    with respx.mock(base_url=_AUDIT_BASE, assert_all_called=False) as mock:
        mock.get("/audit/governance/summary").mock(side_effect=_connect_error)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/governance/summary")

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "upstream_unavailable"
    assert body["upstream"] == "audit-store"


@pytest.mark.asyncio
async def test_audit_store_timeout(app):
    def _timeout(request):
        raise httpx.TimeoutException("timed out", request=request)

    with respx.mock(base_url=_AUDIT_BASE, assert_all_called=False) as mock:
        mock.get("/audit/governance/summary").mock(side_effect=_timeout)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/portal/governance/summary")

    assert response.status_code == 502
    assert response.json()["upstream"] == "audit-store"


@pytest.mark.asyncio
async def test_upstream_validation_error_relayed_unchanged(app):
    with respx.mock(base_url=_AUDIT_BASE, assert_all_called=False) as mock:
        mock.get("/audit/governance/summary").mock(
            return_value=httpx.Response(
                422,
                json={"message": "invalid time parameter(s)", "errors": {"from": "bad"}},
            )
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/portal/governance/summary", params={"from": "not-a-date"}
            )

    assert response.status_code == 422
    assert response.json()["errors"]["from"] == "bad"


@pytest.mark.asyncio
async def test_from_to_params_forwarded(app):
    with respx.mock(base_url=_AUDIT_BASE, assert_all_called=False) as mock:
        route = mock.get("/audit/governance/summary").mock(
            return_value=httpx.Response(200, json=_summary_body())
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/portal/governance/summary",
                params={"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"},
            )

    assert response.status_code == 200
    sent_url = route.calls.last.request.url
    assert sent_url.params["from"] == "2026-01-01T00:00:00Z"
    assert sent_url.params["to"] == "2026-02-01T00:00:00Z"
