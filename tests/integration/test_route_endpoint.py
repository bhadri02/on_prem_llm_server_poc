"""
tests/integration/test_route_endpoint.py

Integration tests for the Intelligent Router POST /route endpoint.

Covers all 7 subtasks (28.1–28.7):
  28.1 — Happy path: cache MISS, inference success → HTTP 200, all WRITE_SET fields populated
  28.2 — Cache HIT: returns cached response, inference NOT called
  28.3 — Governance gate: content_safety_passed=False → HTTP 400, no downstream calls
  28.4 — Health-check failure + fallback success → HTTP 200, fallback_level=1
  28.5 — All backends exhausted: entire fallback chain fails health check → HTTP 503
  28.6 — Inference failure + fallback success → HTTP 200, fallback_level=1
  28.7 — IMF field preservation: non-WRITE_SET fields are byte-identical on output

Requirements: 1.1, 1.2, 1.6, 4.3, 4.5, 5.2, 6.3, 11.1, 11.2

Strategy
--------
`httpx.ASGITransport` does NOT trigger the ASGI lifespan event — it sends
HTTP requests directly. Therefore we bypass the lifespan entirely by:
  1. Calling `create_app()` to get a fresh FastAPI app.
  2. Assigning `app.state.*` attributes directly before starting the client.
  3. The real lifespan never runs, so env-var validation is skipped.

`pytest_httpx.HTTPXMock` intercepts all outgoing calls made by the shared
`httpx.AsyncClient` stored on `app.state.http_client`.
"""

import copy
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

from intelligent_router.main import create_app
from intelligent_router.model_selector import ModelEntry, ModelMatrix
from intelligent_router.task_classifier import ClassifierRules

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_IMF = {
    "request_id": "12345678-1234-4234-8234-123456789012",
    "trace_id": "trace-abc",
    "span_id": "span-def",
    "timestamp_utc": "2024-01-01T00:00:00.000Z",
    "user": {
        "user_id": "test-user",
        "department": "engineering",
        "roles": ["developer"],
        "auth_method": "api_key",
    },
    "request": {
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
        "model": None,
        "task_type": None,
        "stream": False,
        "max_tokens": 512,
        "temperature": 0.7,
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
    "routing": {"selected_model": None, "routing_mode": "auto", "fallback_level": 0},
    "cache": {"lookup_hit": False, "cache_key": None},
    "response": {
        "content": None,
        "finish_reason": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    },
    "metadata": {},
    "extensions": {},
}

CACHE_MISS_RESPONSE = {"hit": False, "cache_key": None}

CACHE_HIT_RESPONSE = {
    "hit": True,
    "cache_key": "abc123",
    "response": {
        "content": "Cached response content",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    },
}


def make_inference_response(imf: dict) -> dict:
    """Return a copy of the IMF with response block populated."""
    result = copy.deepcopy(imf)
    result["response"] = {
        "content": "This is a test response from inference.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    return result


# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

PRIMARY_MODEL_NAME = "llama3.2-3b"
FALLBACK_MODEL_NAME = "llama3.2-3b-fallback"

PRIMARY_HEALTH_URL = "http://inference-ollama:11434/api/tags"
FALLBACK_HEALTH_URL = "http://inference-ollama-fallback:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model_matrix() -> ModelMatrix:
    """Build a minimal two-model matrix (primary + one fallback)."""
    primary_model = ModelEntry(
        name=PRIMARY_MODEL_NAME,
        backend="ollama",
        endpoint="http://inference-ollama:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url=PRIMARY_HEALTH_URL,
        fallback=FALLBACK_MODEL_NAME,
    )
    fallback_model = ModelEntry(
        name=FALLBACK_MODEL_NAME,
        backend="ollama",
        endpoint="http://inference-ollama-fallback:11434",
        tasks=["chat"],
        health_url=FALLBACK_HEALTH_URL,
        fallback=None,
    )
    return ModelMatrix(
        models={
            PRIMARY_MODEL_NAME: primary_model,
            FALLBACK_MODEL_NAME: fallback_model,
        },
        task_defaults={
            "chat": PRIMARY_MODEL_NAME,
            "code": PRIMARY_MODEL_NAME,
            "reasoning": PRIMARY_MODEL_NAME,
            "summarization": PRIMARY_MODEL_NAME,
            "translation": PRIMARY_MODEL_NAME,
        },
    )


def _make_settings() -> MagicMock:
    """Build a minimal mock settings object matching what the pipeline reads."""
    mock_settings = MagicMock()
    mock_settings.cache_url = "http://cache:8086"
    mock_settings.inference_adapter_url = "http://inference-adapter:8087"
    mock_settings.audit_store_url = "http://audit-store:9200"
    mock_settings.inference_timeout_seconds = 30
    mock_settings.health_check_timeout_seconds = 5
    return mock_settings


def _build_app(http_client: httpx.AsyncClient):
    """Create a fresh FastAPI app and populate app.state directly.

    `httpx.ASGITransport` does NOT trigger ASGI lifespan events, so the
    real lifespan (which calls sys.exit if env vars are missing) never runs.
    We set `app.state.*` directly to replicate what the lifespan would do.
    """
    app = create_app()
    app.state.settings = _make_settings()
    app.state.classifier_rules = ClassifierRules(
        rules={"code": ["def ", "function"]}, default="chat"
    )
    app.state.model_matrix = _make_model_matrix()
    app.state.http_client = http_client
    return app


# ---------------------------------------------------------------------------
# 28.1 — Happy path: valid IMF, cache MISS, inference success
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_happy_path_cache_miss_inference_success(httpx_mock: HTTPXMock):
    """HTTP 200, all WRITE_SET fields populated, cache.lookup_hit=False, fallback_level=0."""
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=200)
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    httpx_mock.add_response(
        method="POST",
        url=INFERENCE_URL,
        json=make_inference_response(VALID_IMF),
        status_code=200,
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # WRITE_SET: request.task_type
    assert body["request"]["task_type"] is not None, "request.task_type must be populated by router"

    # WRITE_SET: routing.*
    assert body["routing"]["selected_model"] is not None, "routing.selected_model must be populated"
    assert body["routing"]["routing_mode"] is not None, "routing.routing_mode must be populated"
    assert body["routing"]["fallback_level"] == 0, (
        f"fallback_level must be 0 for primary success, got {body['routing']['fallback_level']}"
    )

    # WRITE_SET: cache.*
    assert body["cache"]["lookup_hit"] is False, "cache.lookup_hit must be False on MISS"
    assert "cache_key" in body["cache"], "cache.cache_key field must be present"


# ---------------------------------------------------------------------------
# 28.2 — Cache HIT: response from cache, inference NOT called
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cache_hit_returns_cached_response(httpx_mock: HTTPXMock):
    """HTTP 200, cache.lookup_hit=True, response.content matches cache, inference NOT called."""
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=200)
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_HIT_RESPONSE, status_code=200
    )
    # Audit fire-and-forget (cache_hit audit)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    # Intentionally do NOT register a response for INFERENCE_URL.
    # If inference is called, pytest-httpx raises — making the test fail,
    # which proves the cache HIT path correctly skips inference.

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["cache"]["lookup_hit"] is True, "cache.lookup_hit must be True on HIT"
    assert body["response"]["content"] == CACHE_HIT_RESPONSE["response"]["content"], (
        "response.content must match the cached value"
    )


# ---------------------------------------------------------------------------
# 28.3 — Governance gate: content_safety_passed=False → HTTP 400, no downstream calls
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_governance_gate_blocks_unsafe_request(httpx_mock: HTTPXMock):
    """HTTP 400 with error='governance_check_failed', no downstream HTTP calls made."""
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    blocked_imf = copy.deepcopy(VALID_IMF)
    blocked_imf["governance"]["content_safety_passed"] = False

    # Register NO mocked responses. Any downstream HTTP call will raise an
    # error from pytest-httpx, causing the test to fail and proving the
    # governance gate is correctly short-circuiting before any downstream call.

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=blocked_imf)

    await http_client.aclose()

    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"] == "governance_check_failed", (
        f"Expected error='governance_check_failed', got {body.get('error')!r}"
    )


# ---------------------------------------------------------------------------
# 28.4 — Primary health check fails, fallback succeeds → HTTP 200, fallback_level=1
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_check_failure_then_fallback_success(httpx_mock: HTTPXMock):
    """Primary model health 503 → fallback model health 200 → inference OK → HTTP 200, fallback_level=1."""
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Primary health check FAILS
    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=503)
    # Fallback audit dispatched for the health-check failure
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)
    # Fallback model health check SUCCEEDS
    httpx_mock.add_response(method="GET", url=FALLBACK_HEALTH_URL, status_code=200)
    # Cache MISS on fallback model
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    # Inference SUCCEEDS on fallback model
    httpx_mock.add_response(
        method="POST",
        url=INFERENCE_URL,
        json=make_inference_response(VALID_IMF),
        status_code=200,
    )
    # Cache write (background task, fire-and-forget)
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    # Routing success audit
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["routing"]["fallback_level"] == 1, (
        f"Expected fallback_level=1 after one health-check fallback, "
        f"got {body['routing']['fallback_level']}"
    )
    assert body["routing"]["selected_model"] == FALLBACK_MODEL_NAME, (
        f"Expected selected_model={FALLBACK_MODEL_NAME!r}, "
        f"got {body['routing']['selected_model']!r}"
    )


# ---------------------------------------------------------------------------
# 28.5 — All backends exhausted: primary + fallback both fail health check → HTTP 503
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_all_backends_exhausted(httpx_mock: HTTPXMock):
    """Both primary and fallback health checks fail → HTTP 503 all_backends_exhausted.

    The test matrix has: primary → fallback (chain_length=2).
    After both fail, fallback_level=1 (advance() called once when primary failed).
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Primary health check FAILS → advance() called → fallback_level=1
    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=503)
    # Fallback audit after primary failure
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)
    # Fallback model health check FAILS → advance() returns None → exhausted
    httpx_mock.add_response(method="GET", url=FALLBACK_HEALTH_URL, status_code=503)
    # Final exhaustion audit
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"] == "all_backends_exhausted", (
        f"Expected error='all_backends_exhausted', got {body.get('error')!r}"
    )
    # chain_length=1 — primary (index 0) advances once to fallback (index 1),
    # which also fails; fallback_level is 1 after the single advance.
    assert body["fallback_level"] == 1, (
        f"Expected fallback_level=1 (chain exhausted after primary+fallback), "
        f"got {body.get('fallback_level')}"
    )


# ---------------------------------------------------------------------------
# 28.6 — Inference failure on primary, fallback inference succeeds → HTTP 200, fallback_level=1
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_inference_failure_then_fallback_success(httpx_mock: HTTPXMock):
    """Primary inference HTTP 500 (InferenceError) → fallback inference 200 → HTTP 200, fallback_level=1."""
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Primary model: health PASSES
    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=200)
    # Cache MISS on primary
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    # Primary inference FAILS with HTTP 500 → InferenceError(reason="non_200")
    httpx_mock.add_response(method="POST", url=INFERENCE_URL, status_code=500)
    # Fallback audit dispatched for the inference failure
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    # Fallback model: health PASSES
    httpx_mock.add_response(method="GET", url=FALLBACK_HEALTH_URL, status_code=200)
    # Cache MISS on fallback model
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    # Fallback inference SUCCEEDS
    httpx_mock.add_response(
        method="POST",
        url=INFERENCE_URL,
        json=make_inference_response(VALID_IMF),
        status_code=200,
    )
    # Cache write (background task)
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    # Routing success audit
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["routing"]["fallback_level"] == 1, (
        f"Expected fallback_level=1 after one inference fallback, "
        f"got {body['routing']['fallback_level']}"
    )


# ---------------------------------------------------------------------------
# 28.7 — IMF field preservation: non-WRITE_SET fields unchanged
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_imf_field_preservation(httpx_mock: HTTPXMock):
    """Non-WRITE_SET fields must be byte-identical on output.

    WRITE_SET (may change): request.task_type, routing.*, cache.*
    Non-WRITE_SET (must be unchanged): request_id, trace_id, span_id,
      user.*, governance.*, request.messages, request.model,
      request.max_tokens, request.temperature, metadata, extensions
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    # Build an IMF with non-null / rich values in all non-WRITE_SET fields
    rich_imf = copy.deepcopy(VALID_IMF)
    rich_imf["trace_id"] = "trace-preserve-check-001"
    rich_imf["span_id"] = "span-preserve-check-001"
    rich_imf["user"] = {
        "user_id": "preserve-user",
        "department": "preserve-dept",
        "roles": ["admin", "developer"],
        "auth_method": "oidc",
    }
    rich_imf["governance"] = {
        "pii_masked": True,
        "pii_fields_detected": ["email"],
        "injection_score": 0.05,
        "jailbreak_score": 0.01,
        "content_safety_passed": True,
        "human_approval_required": False,
        "human_approval_status": "not_required",
        "policy_decisions": [{"policy": "pii_redact", "action": "mask"}],
    }
    rich_imf["request"]["messages"] = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum entanglement."},
    ]
    rich_imf["request"]["model"] = None
    rich_imf["request"]["max_tokens"] = 256
    rich_imf["request"]["temperature"] = 0.3
    rich_imf["metadata"] = {"source": "integration-test", "version": "1.0"}
    rich_imf["extensions"] = {"custom_field": "custom_value", "priority": 5}

    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=200)
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    httpx_mock.add_response(
        method="POST",
        url=INFERENCE_URL,
        json=make_inference_response(rich_imf),
        status_code=200,
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/route", json=rich_imf)

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    out = resp.json()

    # ── Non-WRITE_SET fields: must be identical to what was sent ────────────
    assert out["request_id"] == rich_imf["request_id"], "request_id must be unchanged"
    assert out["trace_id"] == rich_imf["trace_id"], "trace_id must be unchanged"
    assert out["span_id"] == rich_imf["span_id"], "span_id must be unchanged"

    # user block — completely unchanged
    assert out["user"] == rich_imf["user"], "user block must be completely unchanged"

    # governance block — completely unchanged
    assert out["governance"] == rich_imf["governance"], (
        "governance block must be completely unchanged"
    )

    # request sub-fields that are NOT task_type
    assert out["request"]["messages"] == rich_imf["request"]["messages"], (
        "request.messages must be unchanged"
    )
    assert out["request"]["model"] == rich_imf["request"]["model"], (
        "request.model must be unchanged"
    )
    assert out["request"]["max_tokens"] == rich_imf["request"]["max_tokens"], (
        "request.max_tokens must be unchanged"
    )
    assert out["request"]["temperature"] == rich_imf["request"]["temperature"], (
        "request.temperature must be unchanged"
    )

    # envelope fields
    assert out["metadata"] == rich_imf["metadata"], "metadata must be unchanged"
    assert out["extensions"] == rich_imf["extensions"], "extensions must be unchanged"

    # ── WRITE_SET fields: must be populated by the router ───────────────────
    assert out["request"]["task_type"] is not None, (
        "request.task_type must be written by router"
    )
    assert out["routing"]["selected_model"] is not None, (
        "routing.selected_model must be written by router"
    )
    assert out["routing"]["routing_mode"] is not None, (
        "routing.routing_mode must be written by router"
    )
    assert "fallback_level" in out["routing"], (
        "routing.fallback_level must be written by router"
    )
    assert "lookup_hit" in out["cache"], "cache.lookup_hit must be written by router"
    assert "cache_key" in out["cache"], "cache.cache_key must be written by router"
