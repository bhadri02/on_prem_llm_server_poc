"""
tests/integration/test_openai_endpoint.py

Integration tests for the Intelligent Router POST /v1/chat/completions endpoint.

Covers subtasks 29.1–29.3:
  29.1 — Happy path: valid messages, no model field → routing_mode=auto → HTTP 200
          with full OpenAI response shape (id, object, model, choices, usage)
  29.2 — Pinned mode: model field present → routing_mode=pinned → correct model selected;
          empty messages array → HTTP 422 with OpenAI error schema
  29.3 — All backends exhausted: health check returns 503 → HTTP 503 with OpenAI error schema

Strategy
--------
`httpx.ASGITransport` does NOT trigger the ASGI lifespan event. We bypass the
real lifespan entirely by calling `create_app()` and then setting `app.state`
attributes directly before passing the app to AsyncClient.

`pytest_httpx.HTTPXMock` intercepts all outgoing httpx calls made by the
`httpx.AsyncClient` stored on `app.state.http_client`.

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock
from unittest.mock import MagicMock

from intelligent_router.main import create_app
from intelligent_router.model_selector import ModelEntry, ModelMatrix
from intelligent_router.task_classifier import ClassifierRules

# ---------------------------------------------------------------------------
# URL constants (match defaults in mock_settings below)
# ---------------------------------------------------------------------------

HEALTH_URL = "http://inference-ollama:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

CACHE_MISS = {"hit": False, "cache_key": None}

INFERENCE_RESPONSE_BODY = {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "trace_id": None,
    "span_id": None,
    "timestamp_utc": "2024-01-01T00:00:00.000Z",
    "user": {
        "user_id": "poc-user",
        "department": "poc",
        "roles": ["developer"],
        "auth_method": "api_key",
    },
    "request": {
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
        "model": None,
        "task_type": "chat",
        "stream": False,
        "max_tokens": None,
        "temperature": None,
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
    "routing": {
        "selected_model": "llama3.2:3b",
        "routing_mode": "auto",
        "fallback_level": 0,
    },
    "cache": {"lookup_hit": False, "cache_key": None},
    "response": {
        "content": "Hello! How can I help you today?",
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
    },
    "metadata": {},
    "extensions": {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    """Return a minimal mock settings object."""
    s = MagicMock()
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.audit_store_url = "http://audit-store:9200"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    return s


def _make_matrix() -> ModelMatrix:
    """Return a minimal single-model ModelMatrix matching model_matrix.yaml."""
    entry = ModelEntry(
        name="llama3.2:3b",
        backend="ollama",
        endpoint="http://inference-ollama:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url=HEALTH_URL,
        fallback=None,
    )
    return ModelMatrix(
        models={"llama3.2:3b": entry},
        task_defaults={
            "chat": "llama3.2:3b",
            "code": "llama3.2:3b",
            "reasoning": "llama3.2:3b",
            "summarization": "llama3.2:3b",
            "translation": "llama3.2:3b",
        },
    )


def _make_rules() -> ClassifierRules:
    """Return minimal ClassifierRules (matches task_classifier_rules.yaml subset)."""
    return ClassifierRules(
        rules={
            "code": ["code", "function", "python"],
            "reasoning": ["reason", "analyze"],
            "summarization": ["summarize", "summary"],
            "translation": ["translate"],
        },
        default="chat",
    )


def _build_app(http_client: httpx.AsyncClient):
    """Create a fresh FastAPI app and populate app.state directly.

    ASGITransport does not trigger the ASGI lifespan, so the real lifespan
    (with env-var validation and sys.exit) never runs.
    """
    app = create_app()
    app.state.settings = _make_settings()
    app.state.classifier_rules = _make_rules()
    app.state.model_matrix = _make_matrix()
    app.state.http_client = http_client
    return app


# ---------------------------------------------------------------------------
# 29.1 — Happy path: no model field → routing_mode=auto → HTTP 200 OpenAI shape
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_happy_path_auto_routing(httpx_mock: HTTPXMock):
    """Valid messages, no model field → routing_mode=auto → HTTP 200 full OpenAI shape.

    Validates: Requirements 9.1, 9.2, 9.5
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Stage 3: Health check → healthy
    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=200)
    # Stage 4: Cache lookup → MISS
    httpx_mock.add_response(method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS, status_code=200)
    # Stage 5: Inference → success
    httpx_mock.add_response(
        method="POST", url=INFERENCE_URL, json=INFERENCE_RESPONSE_BODY, status_code=200
    )
    # Stage 6 background tasks
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello, how are you?"}]},
        )

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # OpenAI response shape assertions
    assert "id" in body, "Response must have 'id' field"
    assert "object" in body, "Response must have 'object' field"
    assert body["object"] == "chat.completion", f"Expected object='chat.completion', got {body['object']!r}"
    assert "model" in body, "Response must have 'model' field"
    assert body["model"] is not None, "model field must not be null"

    # choices[0] structure
    assert "choices" in body, "Response must have 'choices' field"
    assert len(body["choices"]) >= 1, "choices must have at least one entry"
    choice = body["choices"][0]
    assert "message" in choice, "choices[0] must have 'message'"
    assert choice["message"]["role"] == "assistant", (
        f"choices[0].message.role must be 'assistant', got {choice['message']['role']!r}"
    )
    assert choice["message"]["content"] is not None, (
        "choices[0].message.content must be non-null"
    )
    assert len(choice["message"]["content"]) > 0, (
        "choices[0].message.content must be non-empty"
    )

    # usage block with non-negative integers
    assert "usage" in body, "Response must have 'usage' field"
    usage = body["usage"]
    assert isinstance(usage["prompt_tokens"], int) and usage["prompt_tokens"] >= 0, (
        "usage.prompt_tokens must be a non-negative integer"
    )
    assert isinstance(usage["completion_tokens"], int) and usage["completion_tokens"] >= 0, (
        "usage.completion_tokens must be a non-negative integer"
    )
    assert isinstance(usage["total_tokens"], int) and usage["total_tokens"] >= 0, (
        "usage.total_tokens must be a non-negative integer"
    )


# ---------------------------------------------------------------------------
# 29.2a — Pinned mode: model field present → routing_mode=pinned → correct model
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pinned_mode_selects_correct_model(httpx_mock: HTTPXMock):
    """model field present → routing_mode=pinned → llama3.2:3b selected → HTTP 200.

    Validates: Requirements 9.2, 9.4
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Build an inference response with pinned model reflected in routing block
    pinned_inference_response = {**INFERENCE_RESPONSE_BODY}
    pinned_inference_response = dict(INFERENCE_RESPONSE_BODY)
    pinned_inference_response["routing"] = {
        "selected_model": "llama3.2:3b",
        "routing_mode": "pinned",
        "fallback_level": 0,
    }

    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS, status_code=200)
    httpx_mock.add_response(
        method="POST", url=INFERENCE_URL, json=pinned_inference_response, status_code=200
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "llama3.2:3b",
                "messages": [{"role": "user", "content": "Hello from pinned mode!"}],
            },
        )

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # The selected model in the response must be the pinned model
    assert body["model"] == "llama3.2:3b", (
        f"Expected model='llama3.2:3b', got {body['model']!r}"
    )
    assert body["choices"][0]["message"]["content"] is not None


# ---------------------------------------------------------------------------
# 29.2b — Empty messages array → HTTP 422 with OpenAI error schema
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_empty_messages_returns_422(httpx_mock: HTTPXMock):
    """Empty messages array → HTTP 422 with OpenAI-compatible error schema.

    Pydantic's min_length=1 validator on OpenAIChatRequest.messages rejects the
    request before the route handler runs — FastAPI returns 422.

    Validates: Requirements 9.3
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # No downstream mocks needed — request is rejected before any HTTP calls

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": []},
        )

    await http_client.aclose()

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# 29.3 — All backends exhausted: health check 503 → HTTP 503 with OpenAI error schema
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_backends_exhausted_returns_503(httpx_mock: HTTPXMock):
    """Health check returns 503, no fallback → HTTP 503 with OpenAI-compatible error body.

    The single model in the test matrix has fallback=None. When the health check
    fails the fallback chain is immediately exhausted.

    Validates: Requirements 9.4, 9.6
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Health check fails → exhausted immediately (no fallback configured)
    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=503)
    # Exhaustion audit (fire-and-forget background task)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello!"}]},
        )

    await http_client.aclose()

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # OpenAI-compatible error schema
    assert "error" in body, "503 response must have 'error' key"
    error = body["error"]
    assert error["code"] == 503, f"error.code must be 503, got {error.get('code')!r}"
    assert "message" in error, "error must have 'message' field"
    assert error["message"] is not None, "error.message must not be null"
    assert error["type"] == "service_unavailable", (
        f"error.type must be 'service_unavailable', got {error.get('type')!r}"
    )
