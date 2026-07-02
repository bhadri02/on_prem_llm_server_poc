"""
Property-based tests for IMF field preservation in the Intelligent Router.

Properties covered:
  - Property 4: IMF Field Preservation Invariant
    Every field NOT in WRITE_SET = {request.task_type, routing.*, cache.*}
    is byte-identical to the inbound value after the pipeline runs.
    The governance and user blocks are completely unchanged.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import copy
import json
import types
import uuid
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
from intelligent_router.task_classifier import ClassifierRules
from intelligent_router.model_selector import ModelMatrix, ModelEntry
from intelligent_router.pipeline import run_routing_pipeline
from fastapi import BackgroundTasks


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def _uuid4_strategy():
    """Strategy that generates valid UUID-v4 strings."""
    return st.uuids(version=4).map(str)


def _message_strategy():
    """Strategy for a single IMF message dict."""
    return st.fixed_dictionaries({
        "role": st.sampled_from(["user", "assistant", "system"]),
        "content": st.text(min_size=1, max_size=100),
    })


@st.composite
def valid_imf_strategy(draw):
    """Composite strategy that generates a valid IMF dict.

    Generates random but well-formed values for all preservable fields.
    """
    request_id = draw(_uuid4_strategy())
    trace_id = draw(st.one_of(st.none(), st.text(min_size=1, max_size=40)))
    span_id = draw(st.one_of(st.none(), st.text(min_size=1, max_size=40)))

    messages = draw(st.lists(_message_strategy(), min_size=1, max_size=5))
    max_tokens = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=4096)))
    temperature = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=2.0, allow_nan=False)))

    # Metadata and extensions can be arbitrary JSON-serialisable dicts
    metadata = draw(st.fixed_dictionaries({
        "custom_key": st.one_of(st.none(), st.text(max_size=20)),
    }))
    extensions = draw(st.fixed_dictionaries({
        "ext_key": st.one_of(st.none(), st.integers(min_value=0, max_value=100)),
    }))

    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "user": {
            "user_id": draw(st.text(min_size=1, max_size=30)),
            "department": draw(st.text(min_size=1, max_size=20)),
            "roles": draw(st.lists(st.text(min_size=1, max_size=15), min_size=1, max_size=3)),
            "auth_method": draw(st.sampled_from(["api_key", "oidc", "ldap", "mtls"])),
        },
        "request": {
            "messages": messages,
            "model": None,
            "task_type": None,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        "governance": {
            "pii_masked": draw(st.booleans()),
            "pii_fields_detected": [],
            "injection_score": draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
            "jailbreak_score": draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
            "content_safety_passed": True,  # must be True for pipeline to proceed
            "human_approval_required": False,
            "human_approval_status": "not_required",
            "policy_decisions": [],
        },
        "routing": {
            "selected_model": None,
            "routing_mode": "auto",
            "fallback_level": 0,
        },
        "cache": {
            "lookup_hit": False,
            "cache_key": None,
        },
        "response": {
            "content": None,
            "finish_reason": None,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        },
        "metadata": metadata,
        "extensions": extensions,
    }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_minimal_state():
    """Build a minimal app state for pipeline testing."""
    rules = ClassifierRules(
        rules={
            "code": ["code", "function", "python"],
            "reasoning": ["reason", "analyze"],
            "summarization": ["summarize", "summary"],
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

    state = types.SimpleNamespace(
        classifier_rules=rules,
        model_matrix=matrix,
        http_client=MagicMock(),
        settings=mock_settings,
    )
    return state


async def _run_pipeline_with_mocks(imf_in: dict) -> dict:
    """Run the routing pipeline with mocked Cache (MISS) and Inference (success).

    Returns the resulting IMF dict.
    """
    state = _make_minimal_state()
    background_tasks = BackgroundTasks()

    # Mock check_model_health → always healthy
    # Mock cache_lookup → always MISS
    # Mock call_inference → echoes IMF with a populated response block
    # Mock post_audit_event → no-op

    inferred_imf = copy.deepcopy(imf_in)
    inferred_imf["response"] = {
        "content": "test response content",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }

    with (
        patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
        patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value={"hit": False})),
        patch("intelligent_router.pipeline.call_inference", new=AsyncMock(return_value=inferred_imf)),
        patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
        patch("intelligent_router.pipeline.cache_write", new=AsyncMock()),
    ):
        result = await run_routing_pipeline(imf_in, state, background_tasks)

    return result.imf


# ---------------------------------------------------------------------------
# Fields the pipeline IS allowed to write (WRITE_SET)
# These will NOT be checked for preservation.
# ---------------------------------------------------------------------------
WRITE_SET = {
    # request.task_type is always overwritten
    # routing.* block is always overwritten
    # cache.* block is always overwritten
}

# Top-level keys the router MAY write to
ROUTER_WRITE_TOP_LEVEL = {"routing", "cache", "response"}
ROUTER_WRITE_REQUEST_SUBKEYS = {"task_type"}


def _assert_preserved(original: dict, result: dict, path: str = "") -> None:
    """Recursively assert that all fields NOT in WRITE_SET are preserved."""
    for key in original:
        current_path = f"{path}.{key}" if path else key

        # Skip the blocks the router is allowed to write
        if path == "" and key in ROUTER_WRITE_TOP_LEVEL:
            continue
        if path == "request" and key in ROUTER_WRITE_REQUEST_SUBKEYS:
            continue

        assert key in result, (
            f"Field {current_path!r} was removed from IMF by the pipeline"
        )

        orig_val = original[key]
        res_val = result[key]

        if isinstance(orig_val, dict) and isinstance(res_val, dict):
            _assert_preserved(orig_val, res_val, current_path)
        else:
            # Compare as JSON to handle float precision consistently
            assert json.dumps(orig_val, sort_keys=True) == json.dumps(res_val, sort_keys=True), (
                f"Field {current_path!r} was mutated by the pipeline. "
                f"Original: {orig_val!r}, Result: {res_val!r}"
            )


# ---------------------------------------------------------------------------
# Property 4: IMF Field Preservation Invariant
# ---------------------------------------------------------------------------

@given(imf=valid_imf_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_imf_field_preservation_invariant(imf):
    """**Validates: Requirements 11.1, 11.2, 11.6**

    Property 4: IMF Field Preservation Invariant.

    After running the routing pipeline with a MISS cache and successful
    inference:
      - Every field NOT in {request.task_type, routing.*, cache.*}
        is byte-identical (JSON-serialised) to the inbound value.
      - governance and user blocks are completely unchanged.
    """
    original = copy.deepcopy(imf)

    result_imf = asyncio.run(_run_pipeline_with_mocks(imf))

    # 1. General field preservation (excludes WRITE_SET blocks)
    _assert_preserved(original, result_imf)

    # 2. governance block completely unchanged
    assert result_imf.get("governance") == original.get("governance"), (
        f"governance block was mutated. "
        f"Original: {original.get('governance')!r}, "
        f"Result: {result_imf.get('governance')!r}"
    )

    # 3. user block completely unchanged
    assert result_imf.get("user") == original.get("user"), (
        f"user block was mutated. "
        f"Original: {original.get('user')!r}, "
        f"Result: {result_imf.get('user')!r}"
    )

    # 4. request_id, trace_id, span_id preserved
    assert result_imf.get("request_id") == original.get("request_id")
    assert result_imf.get("trace_id") == original.get("trace_id")
    assert result_imf.get("span_id") == original.get("span_id")

    # 5. metadata and extensions preserved
    assert result_imf.get("metadata") == original.get("metadata")
    assert result_imf.get("extensions") == original.get("extensions")

    # 6. request.messages preserved (router must not alter messages)
    orig_messages = original.get("request", {}).get("messages")
    result_messages = result_imf.get("request", {}).get("messages")
    assert result_messages == orig_messages, (
        f"request.messages was mutated. "
        f"Original: {orig_messages!r}, Result: {result_messages!r}"
    )
