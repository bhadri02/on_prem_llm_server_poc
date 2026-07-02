"""
Property-based tests for Prometheus metrics monotonicity in the Intelligent Router.

Properties covered:
  - Property 10: Metrics Counters Are Monotonically Non-Decreasing
    llm_router_requests_total increases by exactly N after N requests.
    Counter values never decrease between requests.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import copy
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
import intelligent_router.metrics as ir_metrics


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_app_with_state():
    """Create a fresh test app with pre-loaded state.

    httpx.ASGITransport does not fire ASGI lifespan events, so we bypass
    the lifespan entirely and set app.state directly before returning the app.
    """
    rules = ClassifierRules(
        rules={
            "code": ["code", "function"],
            "reasoning": ["reason"],
            "summarization": ["summarize"],
            "translation": ["translate"],
        },
        default="chat",
    )
    model_entry = ModelEntry(
        name="test-model",
        backend="ollama",
        endpoint="http://inference:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url="http://inference:11434/api/tags",
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

    app = create_app()
    # Remove the real lifespan so it does not interfere with test requests
    app.router.lifespan_context = None
    app.state.classifier_rules = rules
    app.state.model_matrix = matrix
    app.state.http_client = MagicMock()
    app.state.settings = mock_settings
    return app


def _make_valid_imf() -> dict:
    """Build a minimal valid IMF payload."""
    import uuid
    return {
        "request_id": str(uuid.uuid4()),
        "trace_id": None,
        "span_id": None,
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "user": {
            "user_id": "test-user",
            "department": "test",
            "roles": ["developer"],
            "auth_method": "api_key",
        },
        "request": {
            "messages": [{"role": "user", "content": "Hello, world!"}],
            "model": None,
            "task_type": None,
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


def _get_requests_total_value() -> float:
    """Get the current total sample sum for llm_router_requests_total."""
    total = 0.0
    for metric in ir_metrics.requests_total.collect():
        for sample in metric.samples:
            if sample.name == "llm_router_requests_total":
                total += sample.value
    return total


def _make_inference_response(imf, *args, **kwargs):
    resp = copy.deepcopy(imf)
    resp["response"] = {
        "content": "Response.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    return resp


# ---------------------------------------------------------------------------
# Property 10: Metrics Counters Are Monotonically Non-Decreasing
# ---------------------------------------------------------------------------

@given(
    n=st.integers(min_value=1, max_value=10),
    outcomes=st.lists(
        st.sampled_from(["cache_hit", "inference_success", "fallback_success", "error"]),
        min_size=1,
        max_size=10,
    ),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_metrics_counters_monotonically_nondecreasing(n, outcomes):
    """**Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6**

    Property 10: Metrics Counters Are Monotonically Non-Decreasing.

    After sending N requests through the router:
      - llm_router_requests_total increases by exactly N (for inference_success outcomes).
      - Counter values never decrease between successive requests.

    Note: We test with inference_success outcomes to keep the test predictable.
    The counter is per-label, so we track the total across all labels.
    """

    async def _run_n_requests(count: int) -> list[float]:
        """Send count inference_success requests and collect counter after each."""
        app = _make_app_with_state()
        transport = httpx.ASGITransport(app=app)

        # Reset metrics before the test
        ir_metrics.requests_total._metrics.clear()

        counter_snapshots = []

        with (
            patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value={"hit": False})),
            patch(
                "intelligent_router.pipeline.call_inference",
                new=AsyncMock(side_effect=_make_inference_response),
            ),
            patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
            patch("intelligent_router.pipeline.cache_write", new=AsyncMock()),
        ):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                for _ in range(count):
                    imf = _make_valid_imf()
                    response = await client.post("/route", json=imf)
                    # Collect current counter total after each request
                    snapshot = _get_requests_total_value()
                    counter_snapshots.append(snapshot)

        return counter_snapshots

    snapshots = asyncio.run(_run_n_requests(n))

    # 1. Counter snapshots are monotonically non-decreasing
    for i in range(1, len(snapshots)):
        assert snapshots[i] >= snapshots[i - 1], (
            f"Counter decreased from {snapshots[i-1]} to {snapshots[i]} "
            f"after request {i+1}"
        )

    # 2. Total counter increased by exactly N after N requests
    # (each successful request increments exactly once)
    total_increase = snapshots[-1] - 0  # started from 0 (reset)
    assert total_increase == n, (
        f"Expected llm_router_requests_total to increase by {n}, "
        f"but total is {total_increase}. Snapshots: {snapshots}"
    )
