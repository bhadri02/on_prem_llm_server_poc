"""
Shared pytest fixtures for the Model Registry, Security & Governance Layer,
and Intelligent Router test suites.

Model Registry fixtures:
  - settings_override: patches STORAGE_PATH and REGISTRY_API_KEY env vars
    and clears the get_settings() lru_cache so every test starts clean.
  - async_client: an httpx.AsyncClient backed by the real FastAPI app with
    the lifespan context manager running (storage loaded, _ready = True).

Security & Governance Layer fixtures:
  - security_test_app: FastAPI app with a mock lifespan (no real env vars
    required, no Presidio loading).
  - security_async_client: httpx.AsyncClient wired to security_test_app.
  - reset_prometheus_registry: autouse fixture that resets security metric
    counters/histograms between tests.
  - mock_router_response: factory fixture for constructing mock router
    responses; companion to patching forward_to_router.

Intelligent Router fixtures:
  - router_test_app: FastAPI app with mock lifespan pre-loaded with
    ClassifierRules, ModelMatrix, and a mock httpx.AsyncClient.
  - router_async_client: httpx.AsyncClient wired to router_test_app via
    ASGITransport (no real network required).
  - reset_router_prometheus_registry: autouse fixture that resets router
    metric counters between tests to prevent counter bleed.
"""

import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from model_registry.config import get_settings
from model_registry.main import app, lifespan

# anyio backend registration (required by pytest-anyio / anyio pytest plugin)
pytest_plugins = ("anyio",)

# ===========================================================================
# Intelligent Router test helpers (shared across property tests)
# ===========================================================================

def _make_router_test_state():
    """Build a minimal app.state for router unit/property tests.

    Returns a SimpleNamespace with:
      - classifier_rules: ClassifierRules loaded from task_classifier_rules.yaml
      - model_matrix:     ModelMatrix with a single model 'test-model'
      - http_client:      MagicMock (async-compatible)
      - settings:         MagicMock with required url/timeout attributes
    """
    import types
    from intelligent_router.task_classifier import ClassifierRules
    from intelligent_router.model_selector import ModelMatrix, ModelEntry
    from intelligent_router.policy import PolicyMatrix

    # Build a minimal ClassifierRules with representative keywords
    rules = ClassifierRules(
        rules={
            "code": ["code", "function", "python", "javascript", "debug", "implement"],
            "reasoning": ["reason", "analyze", "logic", "deduce", "evaluate"],
            "summarization": ["summarize", "summary", "tldr", "brief", "condense"],
            "translation": ["translate", "translation", "in french", "in spanish"],
        },
        default="chat",
    )

    # Build a minimal ModelMatrix with one model
    model_entry = ModelEntry(
        name="test-model",
        backend="ollama",
        endpoint="http://inference-ollama:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url="http://inference-ollama:11434/api/tags",
        fallback=None,
    )
    matrix = ModelMatrix(
        models={"test-model": model_entry},
        task_defaults={
            "chat": "test-model",
            "code": "test-model",
            "reasoning": "test-model",
            "summarization": "test-model",
            "translation": "test-model",
        },
    )

    mock_settings = MagicMock()
    mock_settings.cache_url = "http://cache:8086"
    mock_settings.inference_adapter_url = "http://inference-adapter:8087"
    mock_settings.audit_store_url = "http://audit-store:9200"
    mock_settings.inference_timeout_seconds = 120
    mock_settings.health_check_timeout_seconds = 5

    mock_http_client = MagicMock()

    # Phase 2 — RBAC: permits the roles these tests actually send
    # ("developer", "admin") for every task_type in the test model matrix.
    _all_tasks_allowed = {
        "chat": True,
        "code": True,
        "reasoning": True,
        "summarization": True,
        "translation": True,
    }
    policy_matrix = PolicyMatrix(
        roles={
            "developer": dict(_all_tasks_allowed),
            "admin": dict(_all_tasks_allowed),
        }
    )

    state = types.SimpleNamespace(
        classifier_rules=rules,
        model_matrix=matrix,
        policy_matrix=policy_matrix,
        http_client=mock_http_client,
        settings=mock_settings,
    )
    return state

# Fixed API key used across all tests that exercise auth
TEST_KEY = "test-secret-key"


# ===========================================================================
# Model Registry fixtures (unchanged)
# ===========================================================================


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache on get_settings before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage_path(tmp_path):
    """Return a per-test path for models.json under pytest's tmp_path."""
    return str(tmp_path / "models.json")


@pytest.fixture
def settings_override(storage_path, monkeypatch):
    """
    Monkeypatch STORAGE_PATH and REGISTRY_API_KEY so tests use an isolated
    temporary file and a known API key. Clears lru_cache automatically via
    the clear_settings_cache autouse fixture.
    """
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    monkeypatch.setenv("REGISTRY_API_KEY", TEST_KEY)
    get_settings.cache_clear()
    return storage_path


@pytest.fixture
async def async_client(settings_override):
    """
    Async HTTP client backed by the FastAPI app with lifespan running.

    Starts up the app (loads storage, sets _ready = True) then yields an
    AsyncClient. Tears down the app (sets _ready = False) after the test.

    Requires the anyio pytest backend (registered via pytest_plugins above).
    """
    async with lifespan(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


# ===========================================================================
# Security & Governance Layer fixtures
# ===========================================================================


@pytest.fixture
async def security_test_app(tmp_path):
    """FastAPI app for the Security & Governance Layer with a mock lifespan.

    Bypasses the real lifespan (which calls sys.exit when env vars are
    missing and spins up Presidio). Instead, directly populates app.state
    with minimal test doubles so that route handlers can run.

    PII is disabled (pii_enabled=False) to avoid the heavy Presidio model
    download in tests.
    """
    from security_layer.content_safety import BLOCKLIST
    from security_layer.injection import load_injection_patterns
    from security_layer.main import app as _security_app

    # Write a minimal patterns file
    patterns_file = tmp_path / "injection_patterns.yaml"
    patterns_file.write_text(
        "patterns:\n"
        "  - 'ignore previous instructions'\n"
        "  - 'you are now'\n"
        "  - 'pretend you are'\n"
    )
    patterns = load_injection_patterns(str(patterns_file))

    # Build a mock settings object — all required fields are present
    mock_settings = MagicMock()
    mock_settings.pii_enabled = False
    mock_settings.downstream_router_url = "http://mock-router:8082"
    mock_settings.audit_store_url = "http://mock-audit:9200"
    mock_settings.audit_api_key = "test-key"
    mock_settings.injection_patterns_path = str(patterns_file)
    mock_settings.log_level = "WARNING"

    @asynccontextmanager
    async def _mock_lifespan(application):
        """Replace the real lifespan with a no-op that pre-populates app.state."""
        application.state.settings = mock_settings
        application.state.patterns = patterns
        application.state.analyzer = None   # PII disabled
        application.state.anonymizer = None
        application.state.blocklist = BLOCKLIST
        yield

    # Swap the lifespan on the FastAPI router so ASGITransport uses ours
    original_lifespan = _security_app.router.lifespan_context
    _security_app.router.lifespan_context = _mock_lifespan

    yield _security_app

    # Restore original lifespan so other tests are not affected
    _security_app.router.lifespan_context = original_lifespan


@pytest.fixture
async def security_async_client(security_test_app):
    """Async httpx client wired to the security_test_app via ASGITransport."""
    async with AsyncClient(
        transport=ASGITransport(app=security_test_app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture(autouse=True, scope="function")
def reset_prometheus_registry():
    """Reset Security Layer Prometheus metric counters between tests.

    Clears the internal ``_metrics`` dict on each Counter and Histogram so
    that label-combination child objects from one test do not bleed into the
    next.  This avoids cumulative counter drift across the test session.

    Only touches the four security-layer metrics; model-registry and
    audit-store metrics are left alone.
    """
    import security_layer.metrics as sl_metrics  # noqa: import inside fixture is intentional

    _metric_objects = [
        sl_metrics.requests_total,
        sl_metrics.latency,
        sl_metrics.pii_entities_total,
        sl_metrics.blocks_total,
    ]

    # Clear before the test
    for metric in _metric_objects:
        try:
            metric._metrics.clear()
        except AttributeError:
            pass

    yield

    # Clear after the test as well for defensive cleanup
    for metric in _metric_objects:
        try:
            metric._metrics.clear()
        except AttributeError:
            pass


@pytest.fixture
def mock_router_response():
    """Factory fixture that creates httpx.Response objects for router mocking.

    Usage in tests::

        def test_something(mock_router_response):
            resp = mock_router_response(200, {"request_id": "abc", "response": {"content": "ok"}})

    Returns a callable that accepts ``status_code`` and optional ``body`` and
    produces an ``httpx.Response``.  Use alongside
    ``unittest.mock.patch("security_layer.router_client.forward_to_router", ...)``.
    """
    import json as _json
    import httpx as _httpx

    def _factory(status_code: int = 200, body: dict | None = None) -> _httpx.Response:
        if body is None:
            body = {
                "request_id": "00000000-0000-4000-8000-000000000000",
                "response": {"content": "ok"},
            }
        return _httpx.Response(
            status_code=status_code,
            content=_json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )

    return _factory


# ===========================================================================
# Intelligent Router fixtures (Task 22.1)
# ===========================================================================


@pytest.fixture
async def router_test_app():
    """FastAPI app for the Intelligent Router with a mock lifespan.

    Bypasses the real lifespan (which calls sys.exit when env vars are
    missing). Instead, directly populates app.state with ClassifierRules,
    ModelMatrix, and mock HTTP client.
    """
    from intelligent_router.main import create_app as _create_router_app

    @asynccontextmanager
    async def _mock_router_lifespan(application):
        application.state = _make_router_test_state()
        yield

    _router_app = _create_router_app()
    _router_app.router.lifespan_context = _mock_router_lifespan

    yield _router_app


@pytest.fixture
async def router_async_client(router_test_app):
    """Async httpx client wired to router_test_app via ASGITransport."""
    async with AsyncClient(
        transport=ASGITransport(app=router_test_app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture(autouse=True, scope="function")
def reset_router_prometheus_registry():
    """Reset Intelligent Router Prometheus metric counters between tests.

    Clears the internal ``_metrics`` dict on each Counter and Histogram so
    that label-combination child objects from one test do not bleed into the
    next.
    """
    import intelligent_router.metrics as ir_metrics

    _metric_objects = [
        ir_metrics.requests_total,
        ir_metrics.latency,
        ir_metrics.cache_hits_total,
        ir_metrics.fallbacks_total,
        ir_metrics.errors_total,
        ir_metrics.tokens_total,
        ir_metrics.requests_served_total,
    ]

    for metric in _metric_objects:
        try:
            metric._metrics.clear()
        except AttributeError:
            pass

    yield

    for metric in _metric_objects:
        try:
            metric._metrics.clear()
        except AttributeError:
            pass


@pytest.fixture(autouse=True, scope="function")
def reset_policy_matrix_cache():
    """Clear intelligent_router's TTL-cached policy matrix between tests —
    otherwise one test's fetch (or fallback) result leaks into the next via
    the module-level cache in services/policy_resolver.py."""
    from intelligent_router.services.policy_resolver import reset_cache

    reset_cache()
    yield
    reset_cache()
