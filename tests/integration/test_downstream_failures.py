"""
tests/integration/test_downstream_failures.py

Integration tests for Intelligent Router resilience to downstream failures.

Covers subtask 29.5:
  - Audit Store unavailable (all audit POSTs return 503) →
      caller still gets correct HTTP 200, WARNING logged, no error propagated
  - Cache lookup times out →
      cache.lookup_hit=False, inference proceeds, correct HTTP 200 returned
  - Cache write failure →
      WARNING logged, caller response unaffected (HTTP 200)

Strategy
--------
`httpx.ASGITransport` does NOT trigger the ASGI lifespan. We bypass it by
populating `app.state` directly after calling `create_app()`.

`pytest_httpx.HTTPXMock` intercepts outgoing httpx calls. Timeouts are
injected via `httpx_mock.add_exception(httpx.TimeoutException(...))`.

The custom JSON logger (intelligent_router.logging_config) sets propagate=False
on each logger. To capture log records in tests we temporarily re-enable
propagation on the specific logger-under-test.

Requirements: 8.5, 8.6, 5.4, 7.3
"""

import logging

import httpx
import pytest
from contextlib import contextmanager
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock
from unittest.mock import MagicMock

from intelligent_router.main import create_app
from intelligent_router.model_selector import ModelEntry, ModelMatrix
from intelligent_router.policy import PolicyMatrix
from intelligent_router.task_classifier import ClassifierRules


def _make_policy_matrix() -> PolicyMatrix:
    """Permit "developer" (the role these tests send) for every task_type —
    Phase 2 (RBAC) enforcement isn't what these tests exercise."""
    return PolicyMatrix(
        roles={
            "developer": {
                "chat": True,
                "code": True,
                "reasoning": True,
                "summarization": True,
                "translation": True,
            }
        }
    )

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

HEALTH_URL = "http://inference-ollama:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

CACHE_MISS = {"hit": False, "cache_key": None}

INFERENCE_RESPONSE_BODY = {
    "request_id": "00000000-0000-4000-8000-000000000002",
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
        "messages": [{"role": "user", "content": "Hello!"}],
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


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.audit_store_url = "http://audit-store:9200"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    return s


def _make_matrix() -> ModelMatrix:
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
    """Create a fresh app with app.state populated (no real lifespan)."""
    app = create_app()
    app.state.settings = _make_settings()
    app.state.classifier_rules = _make_rules()
    app.state.model_matrix = _make_matrix()
    app.state.policy_matrix = _make_policy_matrix()
    app.state.http_client = http_client
    return app


@contextmanager
def enable_propagation(*logger_names: str):
    """Temporarily enable propagation on loggers with propagate=False.

    The custom JSON loggers in intelligent_router set propagate=False, which
    prevents pytest's caplog from capturing their records. This context manager
    temporarily restores propagation so caplog works correctly in tests.
    """
    loggers = [logging.getLogger(name) for name in logger_names]
    original = [lg.propagate for lg in loggers]
    for lg in loggers:
        lg.propagate = True
    try:
        yield
    finally:
        for lg, orig in zip(loggers, original):
            lg.propagate = orig

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

HEALTH_URL = "http://inference-ollama:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

CACHE_MISS = {"hit": False, "cache_key": None}

INFERENCE_RESPONSE_BODY = {
    "request_id": "00000000-0000-4000-8000-000000000002",
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
        "messages": [{"role": "user", "content": "Hello!"}],
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


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.audit_store_url = "http://audit-store:9200"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    return s


def _make_matrix() -> ModelMatrix:
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
    """Create a fresh app with app.state populated (no real lifespan)."""
    app = create_app()
    app.state.settings = _make_settings()
    app.state.classifier_rules = _make_rules()
    app.state.model_matrix = _make_matrix()
    app.state.policy_matrix = _make_policy_matrix()
    app.state.http_client = http_client
    return app


# ---------------------------------------------------------------------------
# 29.5.1 — Audit Store unavailable (503) → caller gets HTTP 200, WARNING logged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_audit_store_unavailable_caller_gets_200(httpx_mock: HTTPXMock, caplog):
    """All audit POSTs return 503 → caller still gets HTTP 200.

    The audit POST is dispatched as a BackgroundTask (fire-and-forget). A 503
    response from the audit store must:
      - NOT cause the caller to receive an error
      - Log a WARNING via audit_client

    Validates: Requirements 8.5, 8.6 (audit failures never block the caller)
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS, status_code=200)
    httpx_mock.add_response(
        method="POST", url=INFERENCE_URL, json=INFERENCE_RESPONSE_BODY, status_code=200
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    # Audit POST returns 503 — must be swallowed with WARNING
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=503)

    with caplog.at_level(logging.WARNING, logger="intelligent_router.audit_client"):
        with enable_propagation("intelligent_router.audit_client"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "Hello!"}]},
                )

    await http_client.aclose()

    # Caller must still get HTTP 200 despite audit failure
    assert resp.status_code == 200, (
        f"Audit store 503 must not propagate to caller; got {resp.status_code}: {resp.text}"
    )

    # Response must have valid OpenAI shape
    body = resp.json()
    assert "choices" in body
    assert body["choices"][0]["message"]["content"] is not None

    # A WARNING must have been logged by the audit client
    warning_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "audit" in r.name.lower()
    ]
    assert len(warning_records) > 0, (
        "Expected at least one WARNING from intelligent_router.audit_client "
        f"when audit store returns 503; caplog records: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# 29.5.2 — Cache lookup times out → cache.lookup_hit=False, inference proceeds, HTTP 200
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_lookup_timeout_inference_proceeds(httpx_mock: HTTPXMock, caplog):
    """Cache lookup times out → treated as MISS → inference runs → HTTP 200.

    cache_client.cache_lookup catches TimeoutException, logs WARNING, returns
    {"hit": False}. The pipeline must continue to inference as if it were a MISS.

    Validates: Requirements 5.4 (cache failure → graceful degradation)
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=200)

    # Cache lookup raises TimeoutException — must be caught and treated as MISS
    httpx_mock.add_exception(
        httpx.TimeoutException("cache lookup timed out"),
        method="POST",
        url=CACHE_LOOKUP_URL,
    )

    httpx_mock.add_response(
        method="POST", url=INFERENCE_URL, json=INFERENCE_RESPONSE_BODY, status_code=200
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    with caplog.at_level(logging.WARNING, logger="intelligent_router.cache_client"):
        with enable_propagation("intelligent_router.cache_client"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "Hello!"}]},
                )

    await http_client.aclose()

    # Caller must still get HTTP 200
    assert resp.status_code == 200, (
        f"Cache lookup timeout must not propagate to caller; got {resp.status_code}: {resp.text}"
    )

    # Response must have valid content
    body = resp.json()
    assert "choices" in body
    assert body["choices"][0]["message"]["content"] is not None

    # A WARNING must have been logged by the cache client for the timeout
    warning_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "cache" in r.name.lower()
    ]
    assert len(warning_records) > 0, (
        "Expected at least one WARNING from intelligent_router.cache_client "
        f"on lookup timeout; caplog records: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# 29.5.3 — Cache write failure → WARNING logged, caller response unaffected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_write_failure_caller_unaffected(httpx_mock: HTTPXMock, caplog):
    """Cache write POST returns 503 → WARNING logged, caller still gets HTTP 200.

    cache_write is dispatched as a BackgroundTask. Failure must be swallowed
    with a WARNING — never propagated to the caller.

    Validates: Requirements 7.3 (cache write failure is non-fatal)
    """
    http_client = httpx.AsyncClient()
    app = _build_app(http_client)

    httpx_mock.add_response(method="GET", url=HEALTH_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS, status_code=200)
    httpx_mock.add_response(
        method="POST", url=INFERENCE_URL, json=INFERENCE_RESPONSE_BODY, status_code=200
    )
    # Cache write returns 503 — must be swallowed with WARNING
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=503)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)

    with caplog.at_level(logging.WARNING, logger="intelligent_router.cache_client"):
        with enable_propagation("intelligent_router.cache_client"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "Hello!"}]},
                )

    await http_client.aclose()

    # Caller must still get HTTP 200 despite cache write failure
    assert resp.status_code == 200, (
        f"Cache write failure must not propagate to caller; got {resp.status_code}: {resp.text}"
    )

    # Response must have valid content
    body = resp.json()
    assert "choices" in body
    assert body["choices"][0]["message"]["content"] is not None

    # A WARNING must have been logged by the cache client
    warning_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "cache" in r.name.lower()
    ]
    assert len(warning_records) > 0, (
        "Expected at least one WARNING from intelligent_router.cache_client "
        f"on write failure; caplog records: {[r.message for r in caplog.records]}"
    )
