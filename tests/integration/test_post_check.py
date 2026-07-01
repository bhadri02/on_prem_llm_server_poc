"""
tests/integration/test_post_check.py — Integration tests for POST /security/post-check.

Covers:
  - test_happy_path_pii_in_response    : PII in response.content; mask_text mocked to detect
                                          EMAIL_ADDRESS → 200, content masked, pii_masked=True
  - test_null_response_content         : response.content=None → IMF unchanged, pii_actions=[]
  - test_presidio_exception_degrades   : run_post_pipeline raises → 200 with unmasked content,
                                          pii_masked=False, audit still dispatched
  - test_audit_dispatched_as_background: audit fires as a background task (non-blocking)
  - test_audit_event_type_response_sent: audit event_type="response_sent", outcome="pass"
  - test_audit_x_api_key_*             : Audit POST carries correct X-API-Key (task 24.5)
  - test_no_pii_in_response_content    : clean content → returned unchanged, pii_masked=False
"""

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from security_layer.content_safety import BLOCKLIST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_valid_imf(messages=None, roles=None, response_content=None):
    """Build a minimal valid IMF dict for testing."""
    return {
        "request_id": str(uuid.uuid4()),
        "user": {"user_id": "test-user", "roles": roles or ["developer"]},
        "request": {
            "messages": messages or [{"role": "user", "content": "Hello"}]
        },
        "governance": {
            "pii_masked": False,
            "pii_fields_detected": [],
            "injection_score": 0.0,
            "jailbreak_score": 0.0,
            "content_safety_passed": True,
            "human_approval_required": False,
            "human_approval_status": "not_required",
            "policy_decisions": [],
        },
        "response": (
            {"content": response_content, "finish_reason": None}
            if response_content is not None
            else None
        ),
        "metadata": {},
        "extensions": {},
    }


def _make_client(app):
    """Return a context-manager async client using ASGITransport (no lifespan)."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


def _populate_state(app, audit_api_key: str = "test-audit-key"):
    """Directly set app.state with the attributes that handlers need."""
    mock_settings = MagicMock()
    mock_settings.pii_enabled = False
    mock_settings.downstream_router_url = "http://mock-router:8082"
    mock_settings.audit_store_url = "http://mock-audit:9200"
    mock_settings.audit_api_key = audit_api_key

    patterns = [
        re.compile("ignore previous instructions", re.IGNORECASE),
    ]

    app.state.settings = mock_settings
    app.state.patterns = patterns
    app.state.analyzer = None
    app.state.anonymizer = None
    app.state.blocklist = BLOCKLIST


# ---------------------------------------------------------------------------
# Happy path — PII in response content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_pii_in_response(security_test_app):
    """PII in response.content → masked content returned, pii_masked=True in governance."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content="Contact us at admin@example.com for help.")

    # Mock mask_text to simulate EMAIL_ADDRESS detection
    def _mock_mask_text(text, analyzer, anonymizer, pii_enabled):
        return "[REDACTED_EMAIL_ADDRESS] for help.", ["EMAIL_ADDRESS"]

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ), patch(
        "security_layer.pipeline.mask_text",
        side_effect=_mock_mask_text,
    ):
        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    body = response.json()
    assert "[REDACTED_EMAIL_ADDRESS]" in body["response"]["content"]
    assert body["governance"]["pii_masked"] is True
    assert "EMAIL_ADDRESS" in body["governance"]["pii_fields_detected"]


# ---------------------------------------------------------------------------
# Null response content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_response_content(security_test_app):
    """response.content=None → IMF returned unchanged; audit event has pii_actions=[]."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content=None)
    imf["response"] = {"content": None, "finish_reason": None}

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["content"] is None
    assert body["governance"]["pii_masked"] is False

    # Audit dispatched with empty pii_actions
    mock_audit.assert_awaited_once()
    audit_event, _, _ = mock_audit.call_args.args
    assert audit_event["pii_actions"] == []


# ---------------------------------------------------------------------------
# Presidio exception — graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presidio_exception_degrades_gracefully(security_test_app):
    """Unhandled exception in run_post_pipeline → 200 with unmasked content, pii_masked=False."""
    _populate_state(security_test_app)
    original_content = "Hello from user@example.com"
    imf = make_valid_imf(response_content=original_content)

    with patch(
        "security_layer.routers.post_check.run_post_pipeline",
        new_callable=AsyncMock,
    ) as mock_pipeline, patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        mock_pipeline.side_effect = RuntimeError("Presidio engine crashed")

        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    body = response.json()
    # Content should be unmasked on degraded path
    assert body["response"]["content"] == original_content
    assert body["governance"]["pii_masked"] is False

    # Audit still dispatched on degraded path
    mock_audit.assert_awaited_once()
    audit_event, _, _ = mock_audit.call_args.args
    assert audit_event["pii_actions"] == []


# ---------------------------------------------------------------------------
# Audit dispatched as background task (non-blocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_dispatched_as_background_task(security_test_app):
    """Audit is dispatched via BackgroundTasks and does not block response."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content="The answer is 42.")

    audit_call_count = []

    async def _track_audit(event, url, api_key):
        audit_call_count.append(1)

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        side_effect=_track_audit,
    ):
        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    # Background task ran (ASGITransport executes background tasks before returning)
    assert len(audit_call_count) == 1


# ---------------------------------------------------------------------------
# Audit event payload — event_type and outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_event_type_response_sent_and_outcome_pass(security_test_app):
    """Audit event has event_type='response_sent' and outcome='pass'."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content="Some response text.")

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with _make_client(security_test_app) as client:
            await client.post("/security/post-check", json=imf)

    mock_audit.assert_awaited_once()
    audit_event, _, _ = mock_audit.call_args.args
    assert audit_event["event_type"] == "response_sent"
    assert audit_event["outcome"] == "pass"
    assert audit_event["layer"] == "security"


@pytest.mark.asyncio
async def test_audit_event_contains_request_id(security_test_app):
    """Audit event request_id matches the IMF request_id."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content="Response content.")
    expected_request_id = imf["request_id"]

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with _make_client(security_test_app) as client:
            await client.post("/security/post-check", json=imf)

    audit_event, _, _ = mock_audit.call_args.args
    assert audit_event["request_id"] == expected_request_id


# ---------------------------------------------------------------------------
# 24.5 — Audit X-API-Key header (post-check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_x_api_key_on_post_check(security_test_app):
    """Audit call on post-check carries the configured AUDIT_API_KEY."""
    _populate_state(security_test_app, audit_api_key="post-check-secret")
    imf = make_valid_imf(response_content="Some answer text.")
    expected_api_key = "post-check-secret"

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with _make_client(security_test_app) as client:
            await client.post("/security/post-check", json=imf)

    mock_audit.assert_awaited_once()
    # post_audit_event(event, url, api_key)
    _, audit_url, audit_key = mock_audit.call_args.args
    assert audit_key == expected_api_key


@pytest.mark.asyncio
async def test_audit_x_api_key_on_degraded_path(security_test_app):
    """Audit call on degraded (exception) path also carries the correct AUDIT_API_KEY."""
    _populate_state(security_test_app, audit_api_key="degraded-path-secret")
    imf = make_valid_imf(response_content="Response that causes Presidio to fail.")
    expected_api_key = "degraded-path-secret"

    with patch(
        "security_layer.routers.post_check.run_post_pipeline",
        new_callable=AsyncMock,
    ) as mock_pipeline, patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        mock_pipeline.side_effect = RuntimeError("Boom")

        async with _make_client(security_test_app) as client:
            await client.post("/security/post-check", json=imf)

    mock_audit.assert_awaited_once()
    _, audit_url, audit_key = mock_audit.call_args.args
    assert audit_key == expected_api_key


# ---------------------------------------------------------------------------
# No PII in response content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pii_in_response_content(security_test_app):
    """Clean response content → returned unchanged, pii_masked=False."""
    _populate_state(security_test_app)
    imf = make_valid_imf(response_content="The capital of France is Paris.")

    def _no_pii_mask(text, analyzer, anonymizer, pii_enabled):
        return text, []

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ), patch(
        "security_layer.pipeline.mask_text",
        side_effect=_no_pii_mask,
    ):
        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    body = response.json()
    assert body["response"]["content"] == "The capital of France is Paris."
    assert body["governance"]["pii_masked"] is False


# ---------------------------------------------------------------------------
# IMF with no response block at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imf_with_no_response_block(security_test_app):
    """IMF with response=None → returned as-is with 200."""
    _populate_state(security_test_app)
    imf = make_valid_imf()
    # response is None in this IMF (no response_content arg passed)

    with patch(
        "security_layer.routers.post_check.post_audit_event",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with _make_client(security_test_app) as client:
            response = await client.post("/security/post-check", json=imf)

    assert response.status_code == 200
    body = response.json()
    assert body["governance"]["pii_masked"] is False
    # Audit still dispatched
    mock_audit.assert_awaited_once()
    audit_event, _, _ = mock_audit.call_args.args
    assert audit_event["pii_actions"] == []
