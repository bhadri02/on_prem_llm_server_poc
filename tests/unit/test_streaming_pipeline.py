"""
Unit tests for intelligent_router.pipeline.run_streaming_routing_pipeline.

Mirrors tests/unit/test_pipeline.py's fixtures/conventions for the
non-streaming pipeline. Covers:
1. governance gate blocks -> single "error" line, no downstream calls
2. cache HIT -> "delta" (full cached content) then "done", inference NOT called
3. cache MISS + successful inference -> "delta"s then "done" with populated IMF
4. health-check failure -> falls back to the next healthy model
5. inference error before any delta -> falls back to the next model
6. inference error AFTER a delta already sent -> in-band "error", no fallback
7. all backends exhausted -> "error" line with status_code 503
"""

import json
import os

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import BackgroundTasks

os.environ.setdefault("MODEL_MATRIX_PATH", "/tmp/model_matrix.yaml")
os.environ.setdefault("TASK_RULES_PATH", "/tmp/task_rules.yaml")
os.environ.setdefault("AUDIT_STORE_URL", "http://audit-store:9200")

from intelligent_router.pipeline import run_streaming_routing_pipeline  # noqa: E402
from intelligent_router.inference_client import InferenceError  # noqa: E402
from intelligent_router.model_selector import ModelMatrix, ModelEntry  # noqa: E402
from intelligent_router.policy import PolicyMatrix  # noqa: E402
from intelligent_router.task_classifier import ClassifierRules  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_pipeline.py's mock_state / _base_imf)
# ---------------------------------------------------------------------------


def _make_matrix(*names: str) -> ModelMatrix:
    """Build a ModelMatrix with the given models chained as fallbacks in order."""
    models = {}
    for i, name in enumerate(names):
        fallback = names[i + 1] if i + 1 < len(names) else None
        models[name] = ModelEntry(
            name=name, backend="ollama", endpoint="http://inference-ollama:11434",
            tasks=["chat"], health_url=f"http://inference-ollama:11434/api/tags/{name}",
            fallback=fallback,
        )
    return ModelMatrix(models=models, task_defaults={"chat": names[0], "code": names[0]})


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.settings.cache_url = "http://cache:8086"
    state.settings.inference_adapter_url = "http://inference-adapter:8087"
    state.settings.audit_store_url = "http://audit-store:9200"
    state.settings.inference_timeout_seconds = 120
    state.settings.health_check_timeout_seconds = 5
    state.http_client = AsyncMock()
    state.classifier_rules = ClassifierRules(rules={}, default="chat")
    state.model_matrix = _make_matrix("model-a")
    state.policy_matrix = PolicyMatrix(
        roles={"developer": {"chat": True, "code": True, "reasoning": True,
                              "summarization": True, "translation": True}}
    )
    return state


def _base_imf(content_safety_passed: bool = True) -> dict:
    return {
        "request_id": "11111111-1111-4111-8111-111111111111",
        "user": {"user_id": "test-user", "department": "poc", "roles": ["developer"], "auth_method": "api_key"},
        "request": {"messages": [{"role": "user", "content": "Hello"}], "task_type": "chat", "model": None},
        "governance": {"content_safety_passed": content_safety_passed},
        "routing": {"routing_mode": "auto", "fallback_level": 0, "selected_model": None},
        "cache": {"lookup_hit": False, "cache_key": None},
        "response": {"content": None},
        "metadata": {},
        "extensions": {},
    }


async def _collect(agen):
    return [json.loads(chunk.decode()) async for chunk in agen]


async def _no_op_stream(*args, **kwargs):
    return
    yield  # pragma: no cover — makes this a generator function


def _delta_then_done_stream(pieces: list[str], **done_fields):
    async def _gen(*args, **kwargs):
        for piece in pieces:
            yield {"type": "delta", "content": piece}
        yield {"type": "done", "imf": {"response": {
            "content": "".join(pieces), "finish_reason": "stop",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }, **done_fields}}
    return _gen


def _error_stream(event: str, status_code: int = 502):
    async def _gen(*args, **kwargs):
        yield {"type": "error", "event": event, "status_code": status_code}
    return _gen


def _delta_then_error_stream(pieces: list[str], event: str = "ollama_backend_error"):
    async def _gen(*args, **kwargs):
        for piece in pieces:
            yield {"type": "delta", "content": piece}
        yield {"type": "error", "event": event, "status_code": 502}
    return _gen


# ---------------------------------------------------------------------------
# 1. Governance gate
# ---------------------------------------------------------------------------


class TestGovernanceGate:
    @pytest.mark.asyncio
    async def test_blocked_yields_single_error_line(self, mock_state):
        imf = _base_imf(content_safety_passed=False)
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task") as mock_classify,
            patch("intelligent_router.pipeline.cache_lookup") as mock_cache,
            patch("intelligent_router.pipeline.call_inference_stream") as mock_infer,
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        assert len(lines) == 1
        assert lines[0] == {"type": "error", "event": "governance_check_failed",
                             "status_code": 400, "request_id": imf["request_id"]}
        mock_classify.assert_not_called()
        mock_cache.assert_not_called()
        mock_infer.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Cache hit
# ---------------------------------------------------------------------------


class TestCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_yields_delta_then_done_without_calling_inference(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={
                "hit": True, "cache_key": "abc",
                "response": {"content": "Cached answer", "finish_reason": "stop",
                             "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
            })),
            patch("intelligent_router.pipeline.call_inference_stream") as mock_infer,
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        assert lines[0] == {"type": "delta", "content": "Cached answer"}
        assert lines[1]["type"] == "done"
        assert lines[1]["imf"]["response"]["content"] == "Cached answer"
        assert lines[1]["imf"]["cache"]["lookup_hit"] is True
        mock_infer.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Cache miss + successful inference
# ---------------------------------------------------------------------------


class TestSuccessfulInference:
    @pytest.mark.asyncio
    async def test_deltas_then_done_with_populated_imf(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference_stream",
                  _delta_then_done_stream(["Hello", ", world!"])),
            patch("intelligent_router.pipeline.cache_write", AsyncMock()),
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        deltas = [l for l in lines if l["type"] == "delta"]
        done = [l for l in lines if l["type"] == "done"]
        assert [d["content"] for d in deltas] == ["Hello", ", world!"]
        assert len(done) == 1
        assert done[0]["imf"]["response"]["content"] == "Hello, world!"
        assert done[0]["imf"]["routing"]["selected_model"] == "model-a"

    @pytest.mark.asyncio
    async def test_cache_write_dispatched_on_miss(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference_stream",
                  _delta_then_done_stream(["hi"])),
            patch("intelligent_router.pipeline.cache_write", AsyncMock()) as mock_cache_write,
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))
            await bt()

        mock_cache_write.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Health-check failure -> fallback
# ---------------------------------------------------------------------------


class TestHealthCheckFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_next_healthy_model(self, mock_state):
        mock_state.model_matrix = _make_matrix("model-a", "model-b")
        imf = _base_imf()
        bt = BackgroundTasks()

        health_results = {"model-a": False, "model-b": True}

        async def _fake_health(health_url, *args, **kwargs):
            for name in health_results:
                if name in health_url:
                    return health_results[name]
            return True

        with (
            patch("intelligent_router.pipeline.check_model_health", side_effect=_fake_health),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference_stream",
                  _delta_then_done_stream(["ok"])),
            patch("intelligent_router.pipeline.cache_write", AsyncMock()),
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        done = [l for l in lines if l["type"] == "done"]
        assert len(done) == 1
        assert done[0]["imf"]["routing"]["selected_model"] == "model-b"
        assert done[0]["imf"]["routing"]["fallback_level"] == 1


# ---------------------------------------------------------------------------
# 5. Inference error before any delta -> fallback
# ---------------------------------------------------------------------------


class TestInferenceErrorBeforeDelta:
    @pytest.mark.asyncio
    async def test_falls_back_when_no_content_sent_yet(self, mock_state):
        mock_state.model_matrix = _make_matrix("model-a", "model-b")
        imf = _base_imf()
        bt = BackgroundTasks()

        streams = [_error_stream("ollama_unreachable", 503), _delta_then_done_stream(["recovered"])]

        def _dispatch(*args, **kwargs):
            return streams.pop(0)(*args, **kwargs)

        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference_stream", side_effect=_dispatch),
            patch("intelligent_router.pipeline.cache_write", AsyncMock()),
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        assert not any(l["type"] == "error" for l in lines)
        done = [l for l in lines if l["type"] == "done"]
        assert done[0]["imf"]["routing"]["selected_model"] == "model-b"
        assert done[0]["imf"]["routing"]["fallback_level"] == 1


# ---------------------------------------------------------------------------
# 6. Inference error AFTER a delta -> in-band error, no fallback
# ---------------------------------------------------------------------------


class TestInferenceErrorAfterDelta:
    @pytest.mark.asyncio
    async def test_partial_content_then_error_does_not_fall_back(self, mock_state):
        mock_state.model_matrix = _make_matrix("model-a", "model-b")
        imf = _base_imf()
        bt = BackgroundTasks()

        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference_stream",
                  _delta_then_error_stream(["Once upon a time"])),
            patch("intelligent_router.pipeline.cache_write", AsyncMock()) as mock_cache_write,
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        assert lines[0] == {"type": "delta", "content": "Once upon a time"}
        assert lines[-1]["type"] == "error"
        assert lines[-1]["event"] == "ollama_backend_error"
        # No "done" was ever emitted, and no second model was tried.
        assert not any(l["type"] == "done" for l in lines)
        mock_cache_write.assert_not_called()


# ---------------------------------------------------------------------------
# 7. All backends exhausted
# ---------------------------------------------------------------------------


class TestAllBackendsExhausted:
    @pytest.mark.asyncio
    async def test_all_unhealthy_yields_503_error(self, mock_state):
        mock_state.model_matrix = _make_matrix("model-a")
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.check_model_health", AsyncMock(return_value=False)),
            patch("intelligent_router.pipeline.post_audit_event", AsyncMock()),
        ):
            lines = await _collect(run_streaming_routing_pipeline(imf, mock_state, bt))

        assert len(lines) == 1
        assert lines[0]["type"] == "error"
        assert lines[0]["event"] == "all_backends_exhausted"
        assert lines[0]["status_code"] == 503
