"""
tests/integration/test_chat_endpoint.py

Integration tests for the API Gateway POST /v1/chat/completions endpoint.

Covers task 9.1:
  Test 1 — Non-streaming path: full OpenAI JSON response shape
  Test 2 — Streaming path: SSE chunks proxied correctly
  Test 3 — Downstream timeout → HTTP 502
  Test 4 — Full middleware pipeline emits audit events in correct order

Strategy
--------
- Uses ``starlette.testclient.TestClient`` (sync) for tests 1, 3, and 4.
- Uses ``httpx.AsyncClient`` with ``ASGITransport`` for test 2 (streaming).
- ``api_gateway.routers.chat.forward_to_security`` is patched for non-streaming
  and error path tests. The streaming path patches ``httpx.AsyncClient.stream``.
- ``RateLimitMiddleware._store`` is cleared between tests to prevent leakage.
- ``api_gateway.config.get_settings`` lru_cache is cleared and env vars are
  set via monkeypatch for each test.

Validates: Requirements 5.1–5.5, 6.1–6.5, 7.1–7.5, 9.1–9.7
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from api_gateway.config import get_settings
from api_gateway.schemas.imf import IMFDocument, IMFRequest, IMFResponse, IMFUsage
from api_gateway.services.downstream import DownstreamError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-secret-key"
TEST_DOWNSTREAM_URL = "http://security-layer:8081"

VALID_REQUEST_BODY = {
    "model": "llama3",
    "messages": [{"role": "user", "content": "hello"}],
}

VALID_HEADERS = {"X-Api-Key": TEST_API_KEY}

# A fixed UUID used in the mocked IMFDocument
MOCK_REQUEST_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_imf_document(
    request_id: str = MOCK_REQUEST_ID,
    content: str = "Hello from the model",
    finish_reason: str = "stop",
    model: str = "llama3",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    total_tokens: int = 30,
) -> IMFDocument:
    """Build a minimal IMFDocument that the non-streaming tests mock with."""
    return IMFDocument(
        request_id=request_id,
        trace_id=request_id,
        timestamp_utc="2024-01-01T00:00:00Z",
        request=IMFRequest(model=model),
        response=IMFResponse(
            content=content,
            finish_reason=finish_reason,
            usage=IMFUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_api_gateway_settings_cache():
    """Clear api_gateway.config.get_settings lru_cache before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """Reset RateLimitMiddleware class-level store between tests."""
    from api_gateway.middleware.rate_limit import RateLimitMiddleware
    RateLimitMiddleware._store.clear()
    yield
    RateLimitMiddleware._store.clear()


@pytest.fixture(autouse=True)
def stub_key_resolver(monkeypatch):
    """Stub identity resolution (Phase 2 — RBAC + per-user API keys).

    AuthMiddleware now resolves X-Api-Key against the Admin Portal instead
    of a static comparison. These tests exercise chat-completion mechanics,
    not identity resolution, so TEST_API_KEY resolves to a normal developer
    identity — mirroring the old static-key behaviour — and anything else
    is unresolved.
    """

    async def _fake_resolve_key(key, client):
        from api_gateway.services.key_resolver import KeyProfile

        if key == TEST_API_KEY:
            return KeyProfile(
                user_id="poc-user",
                username="poc-user",
                department="poc",
                roles=["developer"],
                model_entitlements=[],
                key_id="test-key-id",
                rate_limit_override=None,
            )
        return None

    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", _fake_resolve_key)


@pytest.fixture
def env_vars(monkeypatch):
    """Set the required environment variables for the API Gateway app."""
    monkeypatch.setenv("GATEWAY_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", TEST_DOWNSTREAM_URL)
    # Clear again after env vars are set so new Settings() picks them up
    get_settings.cache_clear()


@pytest.fixture
def test_client(env_vars):
    """Return a sync TestClient backed by a freshly created API Gateway app."""
    from api_gateway.main import create_app
    app = create_app()
    # Provide a real (but unused) AsyncClient on app.state so the lifespan
    # does not need to run.  TestClient will trigger lifespan automatically.
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Test 1 — Non-streaming path: full OpenAI JSON response shape
# ---------------------------------------------------------------------------


def test_non_streaming_full_openai_response_shape(test_client):
    """POST /v1/chat/completions (stream=false) returns full OpenAI JSON shape.

    Validates: Requirements 5.1, 6.1–6.4, 7.1, 7.3, 9.1, 9.5
    """
    mock_imf = _make_imf_document()

    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=mock_imf),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers=VALID_HEADERS,
        )

    assert response.status_code == 200, (
        f"Expected HTTP 200, got {response.status_code}: {response.text}"
    )

    body = response.json()

    # id starts with "chatcmpl-"
    assert "id" in body, "Response must have 'id' field"
    assert body["id"].startswith("chatcmpl-"), (
        f"id must start with 'chatcmpl-', got {body['id']!r}"
    )

    # object == "chat.completion"
    assert body.get("object") == "chat.completion", (
        f"object must be 'chat.completion', got {body.get('object')!r}"
    )

    # created is an integer
    assert isinstance(body.get("created"), int), (
        f"created must be an integer, got {type(body.get('created'))}"
    )

    # model == "llama3"
    assert body.get("model") == "llama3", (
        f"model must be 'llama3', got {body.get('model')!r}"
    )

    # choices[0] shape
    choices = body.get("choices", [])
    assert len(choices) >= 1, "choices must have at least one entry"
    choice = choices[0]
    assert choice.get("index") == 0, (
        f"choices[0].index must be 0, got {choice.get('index')!r}"
    )
    message = choice.get("message", {})
    assert message.get("role") == "assistant", (
        f"choices[0].message.role must be 'assistant', got {message.get('role')!r}"
    )
    assert message.get("content") == "Hello from the model", (
        f"choices[0].message.content must be 'Hello from the model', got {message.get('content')!r}"
    )
    assert choice.get("finish_reason") == "stop", (
        f"choices[0].finish_reason must be 'stop', got {choice.get('finish_reason')!r}"
    )

    # usage block
    usage = body.get("usage", {})
    assert usage.get("prompt_tokens") == 10, (
        f"usage.prompt_tokens must be 10, got {usage.get('prompt_tokens')!r}"
    )
    assert usage.get("completion_tokens") == 20, (
        f"usage.completion_tokens must be 20, got {usage.get('completion_tokens')!r}"
    )
    assert usage.get("total_tokens") == 30, (
        f"usage.total_tokens must be 30, got {usage.get('total_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Streaming path: SSE chunks proxied correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_streaming_sse_chunks_proxied(env_vars):
    """POST /v1/chat/completions (stream=true) proxies SSE chunks downstream.

    Uses AsyncClient + ASGITransport so the streaming path can be exercised.
    Patches ``httpx.AsyncClient.stream`` to yield controlled SSE byte chunks.

    Validates: Requirements 5.1, 7.4, 9.5
    """
    # SSE chunks the mock downstream will yield
    sse_chunks = [
        b"data: hello\n\n",
        b"data: world\n\n",
        b"data: [DONE]\n\n",
    ]

    # Build a mock async context manager that yields a mock response whose
    # aiter_bytes() produces our fixed SSE chunks.
    mock_response = MagicMock()
    mock_response.status_code = 200

    async def _aiter_bytes():
        for chunk in sse_chunks:
            yield chunk

    mock_response.aiter_bytes = _aiter_bytes

    @asynccontextmanager
    async def _mock_stream(*args, **kwargs):
        yield mock_response

    from api_gateway.main import create_app
    app = create_app()
    # Pre-populate app.state.http_client so the route handler can access it
    # without the lifespan running (ASGITransport does not run lifespan).
    app.state.http_client = httpx.AsyncClient()

    with patch.object(httpx.AsyncClient, "stream", _mock_stream):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "llama3",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                headers=VALID_HEADERS,
            )
    await app.state.http_client.aclose()

    assert response.status_code == 200, (
        f"Expected HTTP 200, got {response.status_code}: {response.text}"
    )

    content_type = response.headers.get("content-type", "")
    assert "text/event-stream" in content_type, (
        f"Content-Type must contain 'text/event-stream', got {content_type!r}"
    )

    # Verify the proxied chunks appear in the response body
    body_bytes = response.content
    assert b"data: hello\n\n" in body_bytes, "Response body must contain 'data: hello\\n\\n'"
    assert b"data: world\n\n" in body_bytes, "Response body must contain 'data: world\\n\\n'"
    assert b"data: [DONE]\n\n" in body_bytes, "Response body must terminate with 'data: [DONE]\\n\\n'"


# ---------------------------------------------------------------------------
# Test 3 — Downstream timeout → HTTP 502
# ---------------------------------------------------------------------------


def test_downstream_timeout_returns_502(test_client):
    """DownstreamError(502) from forward_to_security → HTTP 502 with error body.

    Validates: Requirements 5.4, 5.5, 7.3, 9.1
    """
    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(side_effect=DownstreamError(502)),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers=VALID_HEADERS,
        )

    assert response.status_code == 502, (
        f"Expected HTTP 502, got {response.status_code}: {response.text}"
    )

    body = response.json()
    assert body == {"error": {"code": "502", "message": "Bad gateway"}}, (
        f"Expected canonical 502 body, got {body!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Full middleware pipeline: audit events emitted in correct order
# ---------------------------------------------------------------------------


def test_full_middleware_pipeline_emits_audit_events_in_order(test_client, capsys):
    """Successful request emits auth_pass → request_received → response_sent in order.

    Parses JSON lines from stdout and filters for audit events (those with
    an 'event_type' field). Asserts the three events for a successful
    non-streaming request are emitted in the correct order.

    Validates: Requirements 9.1–9.7
    """
    mock_imf = _make_imf_document()

    # Clear any stdout captured during app construction / previous tests
    capsys.readouterr()

    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=mock_imf),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers=VALID_HEADERS,
        )

    assert response.status_code == 200, (
        f"Expected HTTP 200 for audit pipeline test, got {response.status_code}: {response.text}"
    )

    captured = capsys.readouterr()
    all_lines = [line.strip() for line in captured.out.splitlines() if line.strip()]

    # Parse all valid JSON lines; filter to those that are audit events
    audit_events = []
    for line in all_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Audit events have 'event_type'; structured log lines do not
        if "event_type" in record:
            audit_events.append(record)

    assert len(audit_events) >= 3, (
        f"Expected at least 3 audit events, got {len(audit_events)}. "
        f"Events found: {[e.get('event_type') for e in audit_events]}"
    )

    event_types = [e["event_type"] for e in audit_events]

    # auth_pass must come before request_received and response_sent
    assert "auth_pass" in event_types, (
        f"Expected 'auth_pass' audit event, got event_types: {event_types}"
    )
    assert "request_received" in event_types, (
        f"Expected 'request_received' audit event, got event_types: {event_types}"
    )
    assert "response_sent" in event_types, (
        f"Expected 'response_sent' audit event, got event_types: {event_types}"
    )

    auth_pass_idx = event_types.index("auth_pass")
    request_received_idx = event_types.index("request_received")
    response_sent_idx = event_types.index("response_sent")

    assert auth_pass_idx < request_received_idx, (
        f"auth_pass (idx={auth_pass_idx}) must come before "
        f"request_received (idx={request_received_idx})"
    )
    assert request_received_idx < response_sent_idx, (
        f"request_received (idx={request_received_idx}) must come before "
        f"response_sent (idx={response_sent_idx})"
    )

    # Verify structural integrity of each audit event
    for event in audit_events:
        assert "audit_id" in event, f"Audit event missing 'audit_id': {event}"
        assert "request_id" in event, f"Audit event missing 'request_id': {event}"
        assert event.get("layer") == "api_gateway", (
            f"Audit event layer must be 'api_gateway', got {event.get('layer')!r}"
        )
        assert event.get("outcome") in ("pass", "block", "error"), (
            f"Audit event outcome must be pass/block/error, got {event.get('outcome')!r}"
        )
