"""
Property-based tests for health endpoint state reflection in the Intelligent Router.

Properties covered:
  - Property 9: Health State Accurately Reflects Loaded Configuration
    GET /health returns HTTP 200 {"status":"ok","rules_loaded":N,"models_loaded":M}
    when both classifier_rules and model_matrix are loaded.
    Returns HTTP 503 {"status":"degraded","reason":...} when either is None.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import types
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from intelligent_router.task_classifier import ClassifierRules
from intelligent_router.model_selector import ModelMatrix, ModelEntry
from intelligent_router.main import create_app


# ---------------------------------------------------------------------------
# Helper: build an app with configurable state
# ---------------------------------------------------------------------------

def _make_app_with_state(classifier_rules, model_matrix):
    """Create a fresh FastAPI app with the given classifier_rules and model_matrix.

    httpx.ASGITransport does not fire ASGI lifespan events, so we bypass
    the lifespan entirely and set app.state directly before returning the app.
    """
    app = create_app()
    # Remove the real lifespan so it does not interfere with test requests
    app.router.lifespan_context = None

    mock_settings = MagicMock()
    app.state.classifier_rules = classifier_rules
    app.state.model_matrix = model_matrix
    app.state.http_client = MagicMock()
    app.state.settings = mock_settings
    return app


def _build_classifier_rules(keyword_count: int) -> ClassifierRules:
    """Build a ClassifierRules with exactly keyword_count total keywords."""
    if keyword_count == 0:
        return ClassifierRules(rules={}, default="chat")

    # Distribute keywords evenly across task types
    task_types = ["code", "reasoning", "summarization", "translation"]
    rules: dict[str, list[str]] = {t: [] for t in task_types}

    for i in range(keyword_count):
        task = task_types[i % len(task_types)]
        rules[task].append(f"keyword_{i}")

    return ClassifierRules(rules=rules, default="chat")


def _build_model_matrix(model_count: int) -> ModelMatrix:
    """Build a ModelMatrix with exactly model_count models."""
    models: dict[str, ModelEntry] = {}
    for i in range(model_count):
        name = f"model-{i}"
        models[name] = ModelEntry(
            name=name,
            backend="ollama",
            endpoint=f"http://inference-{i}:11434",
            tasks=["chat"],
            health_url=f"http://inference-{i}:11434/health",
            fallback=None,
        )

    task_defaults = {"chat": "model-0"} if model_count > 0 else {}
    return ModelMatrix(models=models, task_defaults=task_defaults)


# ---------------------------------------------------------------------------
# Property 9: Health State Accurately Reflects Loaded Configuration
# ---------------------------------------------------------------------------

@given(
    rules_loaded=st.booleans(),
    matrix_loaded=st.booleans(),
    keyword_count=st.integers(min_value=0, max_value=50),
    model_count=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_health_state_reflects_loaded_config(
    rules_loaded, matrix_loaded, keyword_count, model_count
):
    """**Validates: Requirements 10.1, 10.2**

    Property 9: Health State Accurately Reflects Loaded Configuration.

    - When both loaded → HTTP 200 {"status":"ok","rules_loaded":N,"models_loaded":M}
    - When rules not loaded → HTTP 503 {"status":"degraded","reason":"rules_load_failed"}
    - When matrix not loaded → HTTP 503 {"status":"degraded","reason":"matrix_load_failed"}
    """
    classifier_rules = _build_classifier_rules(keyword_count) if rules_loaded else None
    model_matrix = _build_model_matrix(model_count) if matrix_loaded else None

    async def _run():
        app = _make_app_with_state(classifier_rules, model_matrix)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

        return response

    response = asyncio.run(_run())

    if rules_loaded and matrix_loaded:
        # Both loaded → HTTP 200
        assert response.status_code == 200, (
            f"Expected HTTP 200 when both loaded, got {response.status_code}. "
            f"Body: {response.text}"
        )
        body = response.json()
        assert body.get("status") == "ok", (
            f"Expected status='ok', got {body.get('status')!r}"
        )
        assert body.get("rules_loaded") == keyword_count, (
            f"Expected rules_loaded={keyword_count}, got {body.get('rules_loaded')!r}"
        )
        assert body.get("models_loaded") == model_count, (
            f"Expected models_loaded={model_count}, got {body.get('models_loaded')!r}"
        )

    elif not rules_loaded:
        # Rules not loaded → HTTP 503 with rules_load_failed reason
        assert response.status_code == 503, (
            f"Expected HTTP 503 when rules not loaded, got {response.status_code}. "
            f"Body: {response.text}"
        )
        body = response.json()
        assert body.get("status") == "degraded", (
            f"Expected status='degraded', got {body.get('status')!r}"
        )
        assert body.get("reason") == "rules_load_failed", (
            f"Expected reason='rules_load_failed', got {body.get('reason')!r}"
        )

    else:
        # Matrix not loaded → HTTP 503 with matrix_load_failed reason
        assert response.status_code == 503, (
            f"Expected HTTP 503 when matrix not loaded, got {response.status_code}. "
            f"Body: {response.text}"
        )
        body = response.json()
        assert body.get("status") == "degraded", (
            f"Expected status='degraded', got {body.get('status')!r}"
        )
        assert body.get("reason") == "matrix_load_failed", (
            f"Expected reason='matrix_load_failed', got {body.get('reason')!r}"
        )
