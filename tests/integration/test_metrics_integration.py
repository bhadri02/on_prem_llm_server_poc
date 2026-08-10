"""
tests/integration/test_metrics_integration.py

Integration tests for the Intelligent Router Prometheus metrics endpoint.

Covers:
  30.2.1 — GET /metrics returns Content-Type: text/plain; version=0.0.4
  30.2.2 — GET /metrics response body contains all five metric names

Strategy:
  - Perform one successful route invocation to register metric label combinations
    in the default Prometheus registry (counters must be incremented at least
    once to appear in the output).
  - Then query the `metrics_app` ASGI application directly via ASGITransport
    to verify the /metrics endpoint.
  - A per-test fixture clears all five Intelligent Router metric objects'
    internal _metrics dicts to prevent counter bleed between tests.

Pattern follows tests/integration/test_route_endpoint.py:
  - create_app() from intelligent_router.main (no lifespan triggered)
  - app.state.* set directly
  - httpx.AsyncClient with ASGITransport
  - @pytest.mark.anyio decorator
"""

import copy
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

from intelligent_router.main import create_app
from intelligent_router.metrics_app import metrics_app
from intelligent_router.model_selector import ModelEntry, ModelMatrix
from intelligent_router.policy import PolicyMatrix
from intelligent_router.task_classifier import ClassifierRules
import intelligent_router.metrics as ir_metrics


# ---------------------------------------------------------------------------
# Shared test data (same VALID_IMF as test_route_endpoint.py)
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

# URL constants matching the mock settings in _build_route_app()
PRIMARY_MODEL_NAME = "llama3.2:3b"
PRIMARY_HEALTH_URL = "http://inference-ollama:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"

CACHE_MISS_RESPONSE = {"hit": False, "cache_key": None}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_router_metrics():
    """Reset Intelligent Router Prometheus metric counters between tests.

    Clears the internal _metrics dict on each Counter and Histogram so that
    label-combination child objects from one test do not bleed into the next.
    """
    metric_objects = [
        ir_metrics.requests_total,
        ir_metrics.latency,
        ir_metrics.cache_hits_total,
        ir_metrics.fallbacks_total,
        ir_metrics.errors_total,
    ]

    # Clear before the test
    for m in metric_objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass

    yield

    # Clear after the test for defensive cleanup
    for m in metric_objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    """Build a minimal mock settings object matching what the pipeline reads."""
    mock_settings = MagicMock()
    mock_settings.cache_url = "http://cache:8086"
    mock_settings.inference_adapter_url = "http://inference-adapter:8087"
    mock_settings.audit_store_url = "http://audit-store:9200"
    mock_settings.inference_timeout_seconds = 30
    mock_settings.health_check_timeout_seconds = 5
    return mock_settings


def _build_route_app(http_client: httpx.AsyncClient):
    """Create a fresh FastAPI app and populate app.state for route tests.

    Mirrors _build_app() in test_route_endpoint.py — same structure so that
    a single successful route invocation registers metric label combinations
    in the default Prometheus registry.
    """
    app = create_app()
    app.state.settings = _make_settings()
    app.state.classifier_rules = ClassifierRules(
        rules={"code": ["def ", "function"]}, default="chat"
    )
    primary_model = ModelEntry(
        name=PRIMARY_MODEL_NAME,
        backend="ollama",
        endpoint="http://inference-ollama:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url=PRIMARY_HEALTH_URL,
        fallback=None,
    )
    app.state.model_matrix = ModelMatrix(
        models={PRIMARY_MODEL_NAME: primary_model},
        task_defaults={
            "chat": PRIMARY_MODEL_NAME,
            "code": PRIMARY_MODEL_NAME,
            "reasoning": PRIMARY_MODEL_NAME,
            "summarization": PRIMARY_MODEL_NAME,
            "translation": PRIMARY_MODEL_NAME,
        },
    )
    app.state.policy_matrix = PolicyMatrix(
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
    app.state.http_client = http_client
    return app


def _make_inference_response(imf: dict) -> dict:
    """Return a copy of the IMF with the response block populated."""
    result = copy.deepcopy(imf)
    result["response"] = {
        "content": "This is a test response from inference.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    return result


def _mock_successful_route(httpx_mock: HTTPXMock) -> None:
    """Register mock HTTP responses for a full happy-path cache-MISS route."""
    httpx_mock.add_response(method="GET", url=PRIMARY_HEALTH_URL, status_code=200)
    httpx_mock.add_response(
        method="POST", url=CACHE_LOOKUP_URL, json=CACHE_MISS_RESPONSE, status_code=200
    )
    httpx_mock.add_response(
        method="POST",
        url=INFERENCE_URL,
        json=_make_inference_response(VALID_IMF),
        status_code=200,
    )
    httpx_mock.add_response(method="POST", url=CACHE_WRITE_URL, status_code=200)
    httpx_mock.add_response(method="POST", url=AUDIT_URL, status_code=201)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metrics_endpoint_content_type(httpx_mock: HTTPXMock):
    """GET /metrics returns Content-Type: text/plain; version=0.0.4.

    Does one successful route invocation first to ensure at least one
    labelled metric is registered in the default Prometheus registry.
    """
    # 1. Successful route invocation to register metric label combinations
    http_client = httpx.AsyncClient()
    app = _build_route_app(http_client)
    _mock_successful_route(httpx_mock)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        route_resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()
    assert route_resp.status_code == 200, (
        f"Pre-condition failed: route returned {route_resp.status_code}: {route_resp.text}"
    )

    # 2. Query the metrics ASGI app directly.
    # Starlette's Mount redirects /metrics → /metrics/ (307); follow redirects.
    async with AsyncClient(
        transport=ASGITransport(app=metrics_app),
        base_url="http://test",
        follow_redirects=True,
    ) as m_client:
        metrics_resp = await m_client.get("/metrics")

    assert metrics_resp.status_code == 200, (
        f"Expected metrics endpoint to return 200, got {metrics_resp.status_code}"
    )
    content_type = metrics_resp.headers.get("content-type", "")
    assert "text/plain" in content_type, (
        f"Expected Content-Type to contain 'text/plain', got {content_type!r}"
    )
    assert "version=0.0.4" in content_type, (
        f"Expected Content-Type to contain 'version=0.0.4', got {content_type!r}"
    )


@pytest.mark.anyio
async def test_metrics_endpoint_contains_all_five_metric_names(httpx_mock: HTTPXMock):
    """GET /metrics response body contains all five Intelligent Router metric names.

    Does one successful route invocation first to ensure the counters are
    incremented and therefore appear in the Prometheus output.
    """
    expected_metric_names = [
        "llm_router_requests_total",
        "llm_router_latency_seconds",
        "llm_router_cache_hits_total",
        "llm_router_fallbacks_total",
        "llm_router_errors_total",
    ]

    # 1. Successful route invocation to register metric label combinations
    http_client = httpx.AsyncClient()
    app = _build_route_app(http_client)
    _mock_successful_route(httpx_mock)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        route_resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()
    assert route_resp.status_code == 200, (
        f"Pre-condition failed: route returned {route_resp.status_code}: {route_resp.text}"
    )

    # 2. Query the metrics ASGI app directly.
    # Starlette's Mount redirects /metrics → /metrics/ (307); follow redirects.
    async with AsyncClient(
        transport=ASGITransport(app=metrics_app),
        base_url="http://test",
        follow_redirects=True,
    ) as m_client:
        metrics_resp = await m_client.get("/metrics")

    assert metrics_resp.status_code == 200
    body = metrics_resp.text

    for metric_name in expected_metric_names:
        assert metric_name in body, (
            f"Expected metric '{metric_name}' to appear in /metrics output, but it was absent.\n"
            f"Metrics body (first 2000 chars):\n{body[:2000]}"
        )
