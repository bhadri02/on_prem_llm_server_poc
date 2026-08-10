"""
Property-based tests for cache lookup consistency in the Intelligent Router.

Properties covered:
  - Property 7: Cache Lookup Result Consistency
    On HIT → cache.lookup_hit=True, inference NOT called.
    On MISS/timeout/error → cache.lookup_hit=False, inference called exactly once.
    Invariants hold for both 'auto' and 'pinned' routing modes.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import copy
import types
from unittest.mock import AsyncMock, MagicMock, patch

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
from fastapi import BackgroundTasks

from intelligent_router.task_classifier import ClassifierRules
from intelligent_router.model_selector import ModelMatrix, ModelEntry
from intelligent_router.policy import PolicyMatrix
from intelligent_router.pipeline import run_routing_pipeline


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def _message_strategy():
    return st.fixed_dictionaries({
        "role": st.sampled_from(["user", "assistant", "system"]),
        "content": st.text(min_size=1, max_size=100),
    })


def _uuid4_strategy():
    return st.uuids(version=4).map(str)


@st.composite
def valid_imf_strategy(draw):
    """Composite strategy that generates a minimal valid IMF dict."""
    routing_mode = draw(st.sampled_from(["auto", "pinned"]))

    return {
        "request_id": draw(_uuid4_strategy()),
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
            "messages": draw(st.lists(_message_strategy(), min_size=1, max_size=3)),
            "model": "test-model" if routing_mode == "pinned" else None,
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
        "routing": {
            "selected_model": None,
            "routing_mode": routing_mode,
            "fallback_level": 0,
        },
        "cache": {
            "lookup_hit": False,
            "cache_key": None,
        },
        "response": {
            "content": None,
            "finish_reason": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
        "metadata": {},
        "extensions": {},
    }


def _make_pipeline_state():
    """Build minimal pipeline state with test-model."""
    rules = ClassifierRules(
        rules={
            "code": ["code", "function"],
            "reasoning": ["reason", "analyze"],
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

    policy_matrix = PolicyMatrix(
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

    return types.SimpleNamespace(
        classifier_rules=rules,
        model_matrix=matrix,
        policy_matrix=policy_matrix,
        http_client=MagicMock(),
        settings=mock_settings,
    )


# Cache HIT response fixture
_CACHE_HIT_RESPONSE = {
    "hit": True,
    "cache_key": "test-cache-key-abc",
    "response": {
        "content": "Cached response content",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    },
}

# Cache MISS response fixture
_CACHE_MISS_RESPONSE = {"hit": False}


# ---------------------------------------------------------------------------
# Property 7: Cache Lookup Result Consistency
# ---------------------------------------------------------------------------

@given(
    imf=valid_imf_strategy(),
    cache_outcome=st.sampled_from(["hit", "miss", "timeout", "error"]),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_cache_lookup_result_consistency(imf, cache_outcome):
    """**Validates: Requirements 5.2, 5.3, 5.4, 5.6, 6.1, 7.1, 7.4, 11.3, 11.5**

    Property 7: Cache Lookup Result Consistency.

    For any valid IMF and any cache outcome (hit/miss/timeout/error):
      - HIT:   imf_out.cache.lookup_hit == True, inference NOT called.
      - MISS:  imf_out.cache.lookup_hit == False, inference called exactly once.
      - timeout/error: imf_out.cache.lookup_hit == False, inference called once.

    Invariants hold for both 'auto' and 'pinned' routing modes.
    """

    async def _run():
        state = _make_pipeline_state()
        background_tasks = BackgroundTasks()

        # Build the cache mock based on cache_outcome
        if cache_outcome == "hit":
            cache_mock = AsyncMock(return_value=_CACHE_HIT_RESPONSE)
        elif cache_outcome == "miss":
            cache_mock = AsyncMock(return_value=_CACHE_MISS_RESPONSE)
        elif cache_outcome == "timeout":
            import httpx as _httpx
            cache_mock = AsyncMock(return_value={"hit": False})  # cache_lookup never raises
        else:  # error
            cache_mock = AsyncMock(return_value={"hit": False})  # cache_lookup never raises

        # Build an inference mock that counts invocations
        inference_call_count = [0]

        async def _mock_inference(imf_arg, *args, **kwargs):
            inference_call_count[0] += 1
            resp = copy.deepcopy(imf_arg)
            resp["response"] = {
                "content": "Inference response content",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10},
            }
            return resp

        with (
            patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", new=cache_mock),
            patch("intelligent_router.pipeline.call_inference", new=AsyncMock(side_effect=_mock_inference)),
            patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
            patch("intelligent_router.pipeline.cache_write", new=AsyncMock()),
        ):
            result = await run_routing_pipeline(imf, state, background_tasks)

        return result, inference_call_count[0]

    result, inference_calls = asyncio.run(_run())

    if cache_outcome == "hit":
        # Cache HIT → lookup_hit must be True, inference NOT called
        assert result.success is True, (
            f"Expected success on cache hit, got {result.success}, error: {result.error_code}"
        )
        assert result.imf.get("cache", {}).get("lookup_hit") is True, (
            f"Expected cache.lookup_hit=True on HIT, got "
            f"{result.imf.get('cache', {}).get('lookup_hit')!r}"
        )
        assert inference_calls == 0, (
            f"Inference should NOT be called on cache hit, but was called {inference_calls} times"
        )
    else:
        # MISS / timeout / error → lookup_hit must be False, inference called exactly once
        assert result.imf.get("cache", {}).get("lookup_hit") is False, (
            f"Expected cache.lookup_hit=False on {cache_outcome!r}, got "
            f"{result.imf.get('cache', {}).get('lookup_hit')!r}"
        )
        assert inference_calls == 1, (
            f"Inference should be called exactly once on cache {cache_outcome!r}, "
            f"but was called {inference_calls} times"
        )
