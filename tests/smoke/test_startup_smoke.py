"""
tests/smoke/test_startup_smoke.py — Startup smoke tests for the Intelligent Router.

Subtasks covered:
  32.1 — create_app() with valid YAML config runs through lifespan successfully;
          app.state is fully populated and GET /health returns 200.
  32.2 — Startup refusal when required env vars are missing/invalid:
          - MODEL_MATRIX_PATH unset (settings=None) → SystemExit(1)
          - AUDIT_STORE_URL empty            → SystemExit(1)
          - INFERENCE_TIMEOUT_SECONDS=0      → SystemExit(1)
  32.4 — After one route request, the metrics ASGI app serves the correct
          Content-Type and all five llm_router_* metric names.

Strategy
--------
For lifespan tests:   use `app.router.lifespan_context(app)` directly
                      (same pattern as tests/integration/test_router_startup.py).
For HTTP requests:    use httpx.ASGITransport (does NOT trigger lifespan) and
                      set app.state.* manually when needed outside the lifespan context.
For metrics:          import metrics_app from intelligent_router.metrics_app and
                      query it via ASGITransport with follow_redirects=True.
"""

import copy
import shutil
from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

import intelligent_router.main as main_mod
import intelligent_router.metrics as ir_metrics
from intelligent_router.main import create_app
from intelligent_router.metrics_app import metrics_app
from intelligent_router.model_selector import ModelEntry, ModelMatrix
from intelligent_router.policy import PolicyMatrix
from intelligent_router.task_classifier import ClassifierRules

# ---------------------------------------------------------------------------
# URL constants (match mock settings below)
# ---------------------------------------------------------------------------

PRIMARY_MODEL_NAME = "llama3.2:3b"
PRIMARY_HEALTH_URL = "http://inference-ollama:11434/api/tags"
CACHE_LOOKUP_URL = "http://cache:8086/cache/lookup"
CACHE_WRITE_URL = "http://cache:8086/cache/write"
INFERENCE_URL = "http://inference-adapter:8087/infer"
AUDIT_URL = "http://audit-store:9200/audit/events"

CACHE_MISS_RESPONSE = {"hit": False, "cache_key": None}

VALID_IMF = {
    "request_id": "12345678-1234-4234-8234-123456789012",
    "trace_id": "trace-smoke",
    "span_id": "span-smoke",
    "timestamp_utc": "2024-01-01T00:00:00.000Z",
    "user": {
        "user_id": "smoke-user",
        "department": "smoke",
        "roles": ["developer"],
        "auth_method": "api_key",
    },
    "request": {
        "messages": [{"role": "user", "content": "Hello, smoke test."}],
        "model": None,
        "task_type": None,
        "stream": False,
        "max_tokens": 128,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_router_metrics():
    """Reset Intelligent Router Prometheus metric counters between tests.

    Clears the internal _metrics dict on each Counter/Histogram so that
    label-combination child objects from one test do not bleed into the next.
    """
    metric_objects = [
        ir_metrics.requests_total,
        ir_metrics.latency,
        ir_metrics.cache_hits_total,
        ir_metrics.fallbacks_total,
        ir_metrics.errors_total,
    ]
    for m in metric_objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass
    yield
    for m in metric_objects:
        try:
            m._metrics.clear()
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_mock_settings(
    matrix_path: str, rules_path: str, policy_path: str = "policy_matrix.yaml"
) -> MagicMock:
    """Return a fully-valid mock Settings for lifespan startup."""
    s = MagicMock()
    s.model_matrix_path = matrix_path
    s.task_rules_path = rules_path
    s.policy_matrix_path = policy_path
    s.audit_store_url = "http://audit-store:9200"
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    s.log_level = "INFO"
    return s


def _make_settings_mock() -> MagicMock:
    """Build a minimal mock settings object for non-lifespan tests."""
    s = MagicMock()
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.audit_store_url = "http://audit-store:9200"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    return s


def _build_app_with_state(http_client: httpx.AsyncClient):
    """Create a fresh FastAPI app and populate app.state directly.

    ASGITransport does NOT trigger the lifespan, so we bypass env-var
    validation by setting app.state.* ourselves.
    """
    app = create_app()
    app.state.settings = _make_settings_mock()
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
        "content": "Smoke test response content.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }
    return result


# ===========================================================================
# 32.1 — Valid startup: app.state populated and /health returns 200
# ===========================================================================


@pytest.mark.anyio
async def test_valid_startup_populates_app_state_and_health(tmp_path):
    """Valid YAML config → lifespan populates app.state; GET /health returns 200
    with status='ok', rules_loaded > 0, models_loaded > 0.

    Validates: Requirements 10.1
    """
    rules_file = tmp_path / "task_classifier_rules.yaml"
    matrix_file = tmp_path / "model_matrix.yaml"
    policy_file = tmp_path / "policy_matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)
    shutil.copy("policy_matrix.yaml", policy_file)

    mock_settings = _valid_mock_settings(str(matrix_file), str(rules_file), str(policy_file))
    app = create_app()

    with patch.object(main_mod, "settings", mock_settings):
        async with app.router.lifespan_context(app):
            # --- app.state assertions ---
            assert app.state.classifier_rules is not None, (
                "app.state.classifier_rules must be set after valid startup"
            )
            assert app.state.model_matrix is not None, (
                "app.state.model_matrix must be set after valid startup"
            )
            assert app.state.http_client is not None, (
                "app.state.http_client must be set after valid startup"
            )

            # --- GET /health assertions ---
            # ASGITransport inside the lifespan context uses the same app instance
            # whose state was just populated by the lifespan handler above.
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")

    assert resp.status_code == 200, (
        f"Expected /health to return 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "ok", (
        f"Expected status='ok', got {body.get('status')!r}"
    )
    assert body["rules_loaded"] > 0, (
        f"Expected rules_loaded > 0, got {body.get('rules_loaded')}"
    )
    assert body["models_loaded"] > 0, (
        f"Expected models_loaded > 0, got {body.get('models_loaded')}"
    )


# ===========================================================================
# 32.2 — Startup refusal smoke tests
# ===========================================================================


@pytest.mark.anyio
async def test_startup_refuses_when_settings_none():
    """MODEL_MATRIX_PATH unset (settings=None) → lifespan raises SystemExit(1).

    Validates: Requirements 15.1
    """
    app = create_app()
    with patch.object(main_mod, "settings", None):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) when settings=None, got code={exc_info.value.code}"
    )


@pytest.mark.anyio
async def test_startup_refuses_when_audit_store_url_empty(tmp_path):
    """AUDIT_STORE_URL unset/empty → lifespan raises SystemExit(1).

    Validates: Requirements 15.1
    """
    rules_file = tmp_path / "task_classifier_rules.yaml"
    matrix_file = tmp_path / "model_matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)

    mock_settings = _valid_mock_settings(str(matrix_file), str(rules_file))
    mock_settings.audit_store_url = ""  # unset / empty

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) when AUDIT_STORE_URL='', got code={exc_info.value.code}"
    )


@pytest.mark.anyio
async def test_startup_refuses_when_inference_timeout_zero(tmp_path):
    """INFERENCE_TIMEOUT_SECONDS=0 is out of range [1,600] → lifespan raises SystemExit(1).

    Validates: Requirements 15.1
    """
    rules_file = tmp_path / "task_classifier_rules.yaml"
    matrix_file = tmp_path / "model_matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)

    mock_settings = _valid_mock_settings(str(matrix_file), str(rules_file))
    mock_settings.inference_timeout_seconds = 0  # out of range

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) for INFERENCE_TIMEOUT_SECONDS=0, got code={exc_info.value.code}"
    )


# ===========================================================================
# 32.4 — Metrics endpoint smoke test
# ===========================================================================


@pytest.mark.anyio
async def test_metrics_endpoint_after_route_request(httpx_mock: HTTPXMock):
    """After one route request, metrics ASGI app returns correct Content-Type
    and all five llm_router_* metric names are present in the body.

    Validates: Requirements 12.1
    """
    expected_metric_names = [
        "llm_router_requests_total",
        "llm_router_latency_seconds",
        "llm_router_cache_hits_total",
        "llm_router_fallbacks_total",
        "llm_router_errors_total",
    ]

    # --- Step 1: Perform one successful route request to register metrics ---
    http_client = httpx.AsyncClient()
    app = _build_app_with_state(http_client)

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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        route_resp = await client.post("/route", json=VALID_IMF)

    await http_client.aclose()

    assert route_resp.status_code == 200, (
        f"Pre-condition failed: /route returned {route_resp.status_code}: {route_resp.text}"
    )

    # --- Step 2: Query the metrics ASGI app directly ---
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

    # --- Content-Type assertion ---
    content_type = metrics_resp.headers.get("content-type", "")
    assert "text/plain" in content_type, (
        f"Expected Content-Type to contain 'text/plain', got {content_type!r}"
    )
    assert "version=0.0.4" in content_type, (
        f"Expected Content-Type to contain 'version=0.0.4', got {content_type!r}"
    )

    # --- All five metric names in body ---
    body = metrics_resp.text
    for metric_name in expected_metric_names:
        assert metric_name in body, (
            f"Expected metric '{metric_name}' to appear in /metrics output, but it was absent.\n"
            f"Metrics body (first 2000 chars):\n{body[:2000]}"
        )
