"""
tests/integration/test_chat_endpoint.py

Integration tests for the API Gateway POST /v1/chat/completions endpoint.

Covers task 9.1:
  Test 1 — Non-streaming path: full OpenAI JSON response shape
  Test 2 — Streaming path: completed response framed as one SSE chunk
  Test 3 — Downstream timeout → HTTP 502
  Test 4 — Full middleware pipeline emits audit events in correct order
  Test 5 — Audit events are POSTed to the Audit Store, correlated by request_id

Strategy
--------
- Uses ``starlette.testclient.TestClient`` (sync) for all tests, including
  streaming — the streaming path calls the same ``forward_to_security()``
  as non-streaming (nothing downstream produces incremental tokens), so it
  is mocked the same way rather than needing a real streamed httpx response.
- ``app.state.redis`` is swapped for fakeredis so RateLimitMiddleware's
  per-key counters are isolated and deterministic between tests.
- ``api_gateway.config.get_settings`` lru_cache is cleared and env vars are
  set via monkeypatch for each test.

Validates: Requirements 5.1–5.5, 6.1–6.5, 7.1–7.5, 9.1–9.7
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
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
                rate_limit_override=60,
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
    """Return a sync TestClient backed by a freshly created API Gateway app.

    Swaps app.state.redis for fakeredis after the lifespan starts, so
    RateLimitMiddleware's per-key counters are fast and deterministic here
    regardless of whether a real Redis happens to be reachable in this
    environment (these tests aren't exercising rate limiting itself).
    """
    import fakeredis.aioredis

    from api_gateway.main import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as client:
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
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
# Test 1b — Authorization: Bearer accepted as an alternative to X-Api-Key
# ---------------------------------------------------------------------------


def test_authorization_bearer_header_accepted_as_alternative_to_api_key(test_client):
    """POST /v1/chat/completions authenticates via 'Authorization: Bearer <key>',
    with no X-Api-Key header present at all — added so standard OpenAI-compatible
    clients/SDKs (which send Bearer by default) can call this endpoint directly.
    """
    mock_imf = _make_imf_document()

    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=mock_imf),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

    assert response.status_code == 200, (
        f"Expected HTTP 200 via Bearer auth, got {response.status_code}: {response.text}"
    )
    assert response.json().get("model") == "llama3"


def test_x_api_key_takes_precedence_over_authorization_bearer(test_client):
    """When both headers are present, X-Api-Key wins (checked first) —
    a bogus Bearer token alongside a valid X-Api-Key must still authenticate.
    """
    mock_imf = _make_imf_document()

    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=mock_imf),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers={**VALID_HEADERS, "Authorization": "Bearer not-a-real-key"},
        )

    assert response.status_code == 200, (
        f"Expected HTTP 200 (X-Api-Key should win), got {response.status_code}: {response.text}"
    )


def test_missing_both_auth_headers_returns_401(test_client):
    """No X-Api-Key and no Authorization header at all -> 401, unchanged behavior."""
    response = test_client.post(
        "/v1/chat/completions",
        json=VALID_REQUEST_BODY,
    )
    assert response.status_code == 401


def test_malformed_authorization_header_without_bearer_prefix_returns_401(test_client):
    """An Authorization header that isn't 'Bearer <token>' shaped (e.g. 'Basic ...')
    must not be treated as a key — falls through to the existing 401 path."""
    response = test_client.post(
        "/v1/chat/completions",
        json=VALID_REQUEST_BODY,
        headers={"Authorization": f"Basic {TEST_API_KEY}"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 2 — Streaming path: completed response framed as one SSE chunk
# ---------------------------------------------------------------------------


def test_streaming_returns_single_sse_chunk_then_done(test_client):
    """POST /v1/chat/completions (stream=true) uses the same
    forward_to_security() call as the non-streaming path (nothing
    downstream produces incremental tokens), then frames the one
    completed response as a single ``chat.completion.chunk`` SSE event
    followed by ``data: [DONE]``.

    Validates: Requirements 5.1, 7.4, 9.5
    """
    mock_imf = _make_imf_document(content="Hello from the model")
    with patch(
        "api_gateway.routers.chat.forward_to_security",
        new=AsyncMock(return_value=mock_imf),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json={**VALID_REQUEST_BODY, "stream": True},
            headers=VALID_HEADERS,
        )

    assert response.status_code == 200, (
        f"Expected HTTP 200, got {response.status_code}: {response.text}"
    )

    content_type = response.headers.get("content-type", "")
    assert "text/event-stream" in content_type, (
        f"Content-Type must contain 'text/event-stream', got {content_type!r}"
    )

    body_text = response.text
    assert body_text.endswith("data: [DONE]\n\n"), (
        f"Response body must terminate with 'data: [DONE]\\n\\n', got: {body_text!r}"
    )

    data_lines = [
        line[len("data: "):]
        for line in body_text.split("\n\n")
        if line.startswith("data: ") and line[len("data: "):] != "[DONE]"
    ]
    assert len(data_lines) == 1, f"Expected exactly one data event, got: {data_lines!r}"

    chunk = json.loads(data_lines[0])
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"]["content"] == "Hello from the model"
    assert chunk["choices"][0]["delta"]["role"] == "assistant"
    assert chunk["choices"][0]["finish_reason"] == "stop"


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


# ---------------------------------------------------------------------------
# Test 5 — Audit events are POSTed to the Audit Store, correlated by
# request_id, not just written to stdout
# ---------------------------------------------------------------------------


def test_successful_request_posts_correlated_audit_events_to_audit_store(test_client):
    """A successful request must schedule a durable Audit Store write (not
    just the stdout-only emit_audit_event) for auth_pass, request_received,
    and response_sent — all three sharing the SAME request_id, proving
    LoggingMiddleware's generated id is the one actually used end to end
    rather than each layer minting its own uncorrelated id.
    """
    mock_imf = _make_imf_document()
    mock_post = AsyncMock()

    with (
        patch("api_gateway.routers.chat.post_audit_event", new=mock_post),
        patch("api_gateway.services.audit_client.post_audit_event", new=mock_post),
        patch(
            "api_gateway.routers.chat.forward_to_security",
            new=AsyncMock(return_value=mock_imf),
        ),
    ):
        response = test_client.post(
            "/v1/chat/completions",
            json=VALID_REQUEST_BODY,
            headers=VALID_HEADERS,
        )

    assert response.status_code == 200

    posted_events = [call.args[0] for call in mock_post.call_args_list]
    posted_event_types = [e.event_type for e in posted_events]
    assert "auth_pass" in posted_event_types
    assert "request_received" in posted_event_types
    assert "response_sent" in posted_event_types

    request_ids = {e.request_id for e in posted_events}
    assert len(request_ids) == 1, (
        f"Expected all audit events for one request to share one request_id, "
        f"got: {request_ids}"
    )


def test_missing_auth_posts_auth_fail_to_audit_store(test_client):
    """A 401 (no auth header at all) must still schedule an auth_fail POST
    to the Audit Store — this is exactly the class of gateway-layer
    rejection that used to be invisible outside local stdout."""
    mock_post = AsyncMock()
    with (
        patch("api_gateway.services.audit_client.post_audit_event", new=mock_post),
    ):
        response = test_client.post("/v1/chat/completions", json=VALID_REQUEST_BODY)

    assert response.status_code == 401
    posted_events = [call.args[0] for call in mock_post.call_args_list]
    assert any(e.event_type == "auth_fail" for e in posted_events)
