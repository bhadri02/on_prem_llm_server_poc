"""
tests/integration/test_health_router.py

Integration tests for the Intelligent Router GET /health endpoint.

Covers:
  30.1.1 — Returns HTTP 200 with correct rules_loaded / models_loaded counts
  30.1.2 — Returns HTTP 503 degraded when model_matrix is None
  30.1.3 — Returns HTTP 503 degraded when classifier_rules is None
  30.1.4 — Endpoint requires no X-API-Key header
  30.1.5 — No downstream HTTP calls are made by the health endpoint

Pattern follows tests/integration/test_route_endpoint.py:
  - create_app() from intelligent_router.main (no lifespan triggered)
  - app.state.* set directly
  - httpx.AsyncClient with ASGITransport
  - @pytest.mark.anyio decorator
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
# Helpers
# ---------------------------------------------------------------------------


def _build_health_test_app(
    classifier_rules=None,
    model_matrix=None,
    http_client=None,
):
    """Create a fresh FastAPI app and populate app.state for health tests."""
    app = create_app()
    app.state.settings = MagicMock()
    app.state.classifier_rules = classifier_rules
    app.state.model_matrix = model_matrix
    app.state.http_client = http_client or httpx.AsyncClient()
    return app


def _make_classifier_rules(keyword_count: int = 7) -> ClassifierRules:
    """Build a minimal ClassifierRules with a known total keyword count.

    Uses two task types: 'code' (4 keywords) and 'reasoning' (3 keywords)
    regardless of the keyword_count argument — the count is documented for
    callers to assert against total_keyword_count.
    """
    return ClassifierRules(
        rules={
            "code": ["def", "function", "python", "debug"],
            "reasoning": ["analyze", "why", "explain"],
        },
        default="chat",
    )


def _make_model_matrix(model_count: int = 2) -> ModelMatrix:
    """Build a minimal ModelMatrix with a known number of models."""
    models = {}
    for i in range(model_count):
        name = f"model-{i}"
        models[name] = ModelEntry(
            name=name,
            backend="ollama",
            endpoint=f"http://inference-{i}:11434",
            tasks=["chat"],
            health_url=f"http://inference-{i}:11434/api/tags",
            fallback=None,
        )
    task_defaults = {"chat": "model-0"}
    return ModelMatrix(models=models, task_defaults=task_defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_ok_returns_correct_counts():
    """HTTP 200 with rules_loaded and models_loaded matching app.state values."""
    rules = _make_classifier_rules()        # total_keyword_count = 7
    matrix = _make_model_matrix(model_count=2)
    app = _build_health_test_app(classifier_rules=rules, model_matrix=matrix)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body["status"] == "ok", f"Expected status='ok', got {body['status']!r}"
    assert body["rules_loaded"] == rules.total_keyword_count, (
        f"Expected rules_loaded={rules.total_keyword_count}, got {body['rules_loaded']}"
    )
    assert body["models_loaded"] == len(matrix.models), (
        f"Expected models_loaded={len(matrix.models)}, got {body['models_loaded']}"
    )


@pytest.mark.anyio
async def test_health_degraded_when_matrix_is_none():
    """HTTP 503 with reason='matrix_load_failed' when model_matrix is None."""
    rules = _make_classifier_rules()
    app = _build_health_test_app(classifier_rules=rules, model_matrix=None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body["status"] == "degraded", (
        f"Expected status='degraded', got {body['status']!r}"
    )
    assert body["reason"] == "matrix_load_failed", (
        f"Expected reason='matrix_load_failed', got {body['reason']!r}"
    )


@pytest.mark.anyio
async def test_health_degraded_when_rules_is_none():
    """HTTP 503 with reason='rules_load_failed' when classifier_rules is None."""
    matrix = _make_model_matrix(model_count=2)
    app = _build_health_test_app(classifier_rules=None, model_matrix=matrix)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body["status"] == "degraded", (
        f"Expected status='degraded', got {body['status']!r}"
    )
    assert body["reason"] == "rules_load_failed", (
        f"Expected reason='rules_load_failed', got {body['reason']!r}"
    )


@pytest.mark.anyio
async def test_health_requires_no_api_key():
    """GET /health returns HTTP 200 without any X-API-Key header (no auth required)."""
    rules = _make_classifier_rules()
    matrix = _make_model_matrix(model_count=3)
    app = _build_health_test_app(classifier_rules=rules, model_matrix=matrix)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Explicitly send no auth headers
        resp = await client.get("/health")

    assert resp.status_code == 200, (
        f"Expected 200 without auth header, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.anyio
async def test_health_makes_no_downstream_calls(httpx_mock: HTTPXMock):
    """GET /health returns 200 without triggering any outgoing HTTP calls.

    Uses HTTPXMock with no registered responses — any outgoing call would
    raise an error, which would cause the test to fail and prove that a
    downstream call was made.
    """
    rules = _make_classifier_rules()
    matrix = _make_model_matrix(model_count=2)

    # The shared http_client used by the router pipeline routes through httpx_mock
    http_client = httpx.AsyncClient()
    app = _build_health_test_app(
        classifier_rules=rules,
        model_matrix=matrix,
        http_client=http_client,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    await http_client.aclose()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Assert zero outgoing HTTP calls were recorded
    requests_made = httpx_mock.get_requests()
    assert len(requests_made) == 0, (
        f"Expected 0 downstream calls from /health, but {len(requests_made)} were made: "
        f"{[str(r.url) for r in requests_made]}"
    )
