"""
Unit tests for GET /audit/governance/summary (audit_store).

Covers: blocked-reason breakdown (including the security_layer error_code
fix and the previously-missing policy_denied/model_not_entitled EventTypeEnum
values), injection-flagged count, PII detection count, token totals, and
per-model usage counts.
"""

from contextlib import asynccontextmanager

import httpx
import pytest

from audit_store.database import get_connection, init_schema
from audit_store.main import create_app

AUDIT_API_KEY = "test-key"


def _make_app():
    @asynccontextmanager
    async def _noop_lifespan(application):
        yield

    application = create_app()
    application.router.lifespan_context = _noop_lifespan

    conn = get_connection(":memory:")
    init_schema(conn)

    class _TestSettings:
        audit_api_key: str = AUDIT_API_KEY
        db_path: str = ":memory:"

    application.state.conn = conn
    application.state.settings = _TestSettings()
    return application


def _make_client(application):
    transport = httpx.ASGITransport(app=application)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": AUDIT_API_KEY},
    )


def _event(request_id: str, **overrides) -> dict:
    payload = {
        "request_id": request_id,
        "layer": "security",
        "event_type": "request_received",
        "outcome": "pass",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_empty_db_returns_zeroed_summary():
    app = _make_app()
    async with _make_client(app) as client:
        resp = await client.get("/audit/governance/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_events"] == 0
        assert body["requests_blocked_total"] == 0
        assert body["blocked_by_reason"] == {}
        assert body["injection_flagged_total"] == 0
        assert body["pii_detections_total"] == 0
        assert body["token_usage"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        assert body["model_usage"] == {}


@pytest.mark.asyncio
async def test_blocked_by_reason_uses_error_code_over_event_type():
    """security_layer's block events carry error_code=block_reason; router's
    policy/entitlement denials carry no error_code, so event_type is used as
    the fallback reason label."""
    import uuid

    app = _make_app()
    async with _make_client(app) as client:
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="security",
                event_type="security_block",
                outcome="block",
                error_code="injection_detected",
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="security",
                event_type="security_block",
                outcome="block",
                error_code="content_safety_violation",
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="router",
                event_type="policy_denied",
                outcome="block",
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="router",
                event_type="model_not_entitled",
                outcome="block",
            ),
        )

        resp = await client.get("/audit/governance/summary")
        assert resp.status_code == 200
        body = resp.json()

        assert body["requests_blocked_total"] == 4
        assert body["blocked_by_reason"] == {
            "injection_detected": 1,
            "content_safety_violation": 1,
            "policy_denied": 1,
            "model_not_entitled": 1,
        }
        assert body["injection_flagged_total"] == 1


@pytest.mark.asyncio
async def test_pii_detections_and_token_usage_and_model_usage():
    import uuid

    app = _make_app()
    async with _make_client(app) as client:
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="security",
                event_type="request_received",
                outcome="pass",
                pii_actions=["EMAIL_ADDRESS", "PHONE_NUMBER"],
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="router",
                event_type="inference_complete",
                outcome="pass",
                model_used="llama3.2:3b",
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="router",
                event_type="cache_hit",
                outcome="pass",
                model_used="llama3.2:3b",
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                layer="router",
                event_type="inference_complete",
                outcome="pass",
                model_used="qwen2.5:3b",
                prompt_tokens=20,
                completion_tokens=10,
            ),
        )

        resp = await client.get("/audit/governance/summary")
        assert resp.status_code == 200
        body = resp.json()

        assert body["pii_detections_total"] == 2
        assert body["token_usage"] == {
            "prompt_tokens": 220,
            "completion_tokens": 110,
            "total_tokens": 330,
        }
        assert body["model_usage"] == {"llama3.2:3b": 2, "qwen2.5:3b": 1}


@pytest.mark.asyncio
async def test_time_range_filters_events():
    import uuid

    app = _make_app()
    async with _make_client(app) as client:
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                timestamp_utc="2020-01-01T00:00:00.000Z",
                outcome="block",
                event_type="security_block",
                error_code="injection_detected",
            ),
        )
        await client.post(
            "/audit/events",
            json=_event(
                str(uuid.uuid4()),
                timestamp_utc="2025-01-01T00:00:00.000Z",
                outcome="block",
                event_type="security_block",
                error_code="policy_denied",
            ),
        )

        resp = await client.get(
            "/audit/governance/summary",
            params={"from": "2024-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_events"] == 1
        assert body["blocked_by_reason"] == {"policy_denied": 1}


@pytest.mark.asyncio
async def test_invalid_time_range_returns_422():
    app = _make_app()
    async with _make_client(app) as client:
        resp = await client.get(
            "/audit/governance/summary", params={"from": "not-a-date"}
        )
        assert resp.status_code == 422
