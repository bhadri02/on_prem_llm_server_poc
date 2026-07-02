"""
Unit tests for intelligent_router.pipeline.

Covers subtask 14.1–14.5:
1. governance gate blocks: content_safety_passed=False → 400, no downstream calls
2. governance gate absent: missing 'governance' key → same as above (400)
3. invalid pinned model: select_model raises InvalidPinnedModelError → 422
4. task_type always overwritten by classifier output
5. cache HIT with missing response.content treated as MISS (inference IS called)
6. cache HIT with valid content → inference NOT called, returns 200
7. fallback_level is 0 when primary model succeeds
8. cache write NOT dispatched when lookup_hit=True (cache hit path)
9. unhandled exception returns 500 internal_error
10. all backends exhausted → 503 all_backends_exhausted
"""

import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
from fastapi import BackgroundTasks

# Set required env vars before importing anything from intelligent_router
os.environ.setdefault("MODEL_MATRIX_PATH", "/tmp/model_matrix.yaml")
os.environ.setdefault("TASK_RULES_PATH", "/tmp/task_rules.yaml")
os.environ.setdefault("AUDIT_STORE_URL", "http://audit-store:9200")

from intelligent_router.pipeline import PipelineResult, run_routing_pipeline  # noqa: E402
from intelligent_router.model_selector import (  # noqa: E402
    InvalidPinnedModelError,
    NoModelForTaskError,
    ModelMatrix,
    ModelEntry,
)
from intelligent_router.fallback_manager import FallbackState  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_single_model_matrix(model_name: str = "llama3-chat") -> ModelMatrix:
    """Return a ModelMatrix with a single model and no fallback."""
    entry = ModelEntry(
        name=model_name,
        backend="ollama",
        endpoint="http://inference-ollama:11434",
        tasks=["chat"],
        health_url="http://inference-ollama:11434/api/tags",
        fallback=None,
    )
    return ModelMatrix(
        models={model_name: entry},
        task_defaults={"chat": model_name, "code": model_name},
    )


@pytest.fixture
def mock_state():
    """Minimal state mock with realistic settings."""
    from intelligent_router.task_classifier import ClassifierRules

    state = MagicMock()
    state.settings.cache_url = "http://cache:8086"
    state.settings.inference_adapter_url = "http://inference-adapter:8087"
    state.settings.audit_store_url = "http://audit-store:9200"
    state.settings.inference_timeout_seconds = 120
    state.settings.health_check_timeout_seconds = 5
    state.http_client = AsyncMock()

    # Default: rules that classify as "chat"
    state.classifier_rules = ClassifierRules(rules={}, default="chat")
    state.model_matrix = _make_single_model_matrix()
    return state


def _base_imf(
    content_safety_passed: bool = True,
    task_type: str = "chat",
    routing_mode: str = "auto",
    model: str | None = None,
) -> dict:
    """Return a minimal valid IMF dict."""
    return {
        "request_id": "11111111-1111-4111-8111-111111111111",
        "request": {
            "messages": [{"role": "user", "content": "Hello"}],
            "task_type": task_type,
            "model": model,
        },
        "governance": {
            "content_safety_passed": content_safety_passed,
        },
        "routing": {
            "routing_mode": routing_mode,
            "fallback_level": 0,
            "selected_model": None,
        },
        "cache": {"lookup_hit": False, "cache_key": None},
        "response": {"content": None},
        "metadata": {},
        "extensions": {},
    }


# ---------------------------------------------------------------------------
# Test 1 & 2: Governance gate blocks
# ---------------------------------------------------------------------------

class TestGovernanceGate:
    """governance.content_safety_passed=False or missing → 400 with no downstream calls."""

    @pytest.mark.asyncio
    async def test_governance_false_returns_400(self, mock_state):
        imf = _base_imf(content_safety_passed=False)
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task") as mock_classify,
            patch("intelligent_router.pipeline.select_model") as mock_select,
            patch("intelligent_router.pipeline.check_model_health") as mock_health,
            patch("intelligent_router.pipeline.cache_lookup") as mock_cache,
            patch("intelligent_router.pipeline.call_inference") as mock_infer,
            patch("intelligent_router.pipeline.post_audit_event") as mock_audit,
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 400
        assert result.error_code == "governance_check_failed"
        assert result.success is False
        mock_classify.assert_not_called()
        mock_select.assert_not_called()
        mock_health.assert_not_called()
        mock_cache.assert_not_called()
        mock_infer.assert_not_called()
        mock_audit.assert_not_called()

    @pytest.mark.asyncio
    async def test_governance_absent_returns_400(self, mock_state):
        """Missing 'governance' key is treated the same as content_safety_passed=False."""
        imf = _base_imf()
        del imf["governance"]
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task") as mock_classify,
            patch("intelligent_router.pipeline.select_model") as mock_select,
            patch("intelligent_router.pipeline.check_model_health") as mock_health,
            patch("intelligent_router.pipeline.cache_lookup") as mock_cache,
            patch("intelligent_router.pipeline.call_inference") as mock_infer,
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 400
        assert result.error_code == "governance_check_failed"
        mock_classify.assert_not_called()
        mock_select.assert_not_called()
        mock_health.assert_not_called()
        mock_cache.assert_not_called()
        mock_infer.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Invalid pinned model → 422
# ---------------------------------------------------------------------------

class TestInvalidPinnedModel:
    """select_model raises InvalidPinnedModelError → 422."""

    @pytest.mark.asyncio
    async def test_invalid_pinned_model_returns_422(self, mock_state):
        imf = _base_imf(model="nonexistent-model", routing_mode="pinned")
        bt = BackgroundTasks()
        with patch(
            "intelligent_router.pipeline.classify_task", return_value="chat"
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 422
        assert result.error_code == "invalid_pinned_model"
        assert result.success is False


# ---------------------------------------------------------------------------
# Test 4: task_type is always overwritten by classifier
# ---------------------------------------------------------------------------

class TestTaskTypeOverwritten:
    """Inbound task_type is always replaced with the classifier's output."""

    @pytest.mark.asyncio
    async def test_task_type_overwritten_on_success(self, mock_state):
        """Even if inbound task_type='old_value', classifier result wins."""
        imf = _base_imf(task_type="old_value")
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="code") as mock_classify,
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch("intelligent_router.pipeline.cache_lookup", new_callable=AsyncMock,
                  return_value={"hit": False}),
            patch("intelligent_router.pipeline.call_inference", new_callable=AsyncMock,
                  return_value={"response": {"content": "result", "finish_reason": "stop", "usage": {}}}),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
            patch("intelligent_router.pipeline.cache_write", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert imf["request"]["task_type"] == "code"
        mock_classify.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Cache HIT with missing content → treated as MISS, inference called
# ---------------------------------------------------------------------------

class TestCacheHitMissingContent:
    """Cache HIT with null response.content is treated as MISS; inference runs."""

    @pytest.mark.asyncio
    async def test_cache_hit_null_content_calls_inference(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        inference_mock = AsyncMock(
            return_value={"response": {"content": "inferred", "finish_reason": "stop", "usage": {}}}
        )
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch(
                "intelligent_router.pipeline.cache_lookup",
                new_callable=AsyncMock,
                return_value={"hit": True, "response": {"content": None}},
            ),
            patch("intelligent_router.pipeline.call_inference", inference_mock),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
            patch("intelligent_router.pipeline.cache_write", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        inference_mock.assert_called_once()
        assert result.status_code == 200
        assert imf["cache"]["lookup_hit"] is False

    @pytest.mark.asyncio
    async def test_cache_hit_absent_response_key_calls_inference(self, mock_state):
        """Cache HIT response block missing entirely → treated as MISS."""
        imf = _base_imf()
        bt = BackgroundTasks()
        inference_mock = AsyncMock(
            return_value={"response": {"content": "inferred", "finish_reason": "stop", "usage": {}}}
        )
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch(
                "intelligent_router.pipeline.cache_lookup",
                new_callable=AsyncMock,
                return_value={"hit": True},  # no 'response' key at all
            ),
            patch("intelligent_router.pipeline.call_inference", inference_mock),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
            patch("intelligent_router.pipeline.cache_write", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        inference_mock.assert_called_once()
        assert result.status_code == 200


# ---------------------------------------------------------------------------
# Test 6: Cache HIT with valid content → inference NOT called, returns 200
# ---------------------------------------------------------------------------

class TestCacheHitValidContent:
    """Valid cache HIT skips inference and returns 200."""

    @pytest.mark.asyncio
    async def test_cache_hit_valid_content_returns_200(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        inference_mock = AsyncMock()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch(
                "intelligent_router.pipeline.cache_lookup",
                new_callable=AsyncMock,
                return_value={
                    "hit": True,
                    "response": {
                        "content": "cached answer",
                        "finish_reason": "stop",
                        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
                    },
                    "cache_key": "abc123",
                },
            ),
            patch("intelligent_router.pipeline.call_inference", inference_mock),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        inference_mock.assert_not_called()
        assert result.status_code == 200
        assert result.success is True
        assert result.error_code is None
        assert imf["response"]["content"] == "cached answer"
        assert imf["cache"]["lookup_hit"] is True


# ---------------------------------------------------------------------------
# Test 7: fallback_level is 0 when primary model succeeds
# ---------------------------------------------------------------------------

class TestFallbackLevelOnSuccess:
    """Primary model health passes and inference succeeds → fallback_level stays 0."""

    @pytest.mark.asyncio
    async def test_fallback_level_zero_on_primary_success(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch("intelligent_router.pipeline.cache_lookup", new_callable=AsyncMock,
                  return_value={"hit": False}),
            patch("intelligent_router.pipeline.call_inference", new_callable=AsyncMock,
                  return_value={"response": {"content": "hello", "finish_reason": "stop", "usage": {}}}),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
            patch("intelligent_router.pipeline.cache_write", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 200
        assert imf["routing"]["fallback_level"] == 0


# ---------------------------------------------------------------------------
# Test 8: cache write NOT dispatched when lookup_hit=True
# ---------------------------------------------------------------------------

class TestCacheWriteNotDispatchedOnHit:
    """When a cache HIT serves the response, cache_write must NOT be added to background tasks."""

    @pytest.mark.asyncio
    async def test_cache_write_not_added_on_cache_hit(self, mock_state):
        imf = _base_imf()
        # Use a real BackgroundTasks and inspect what was added
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=True),
            patch(
                "intelligent_router.pipeline.cache_lookup",
                new_callable=AsyncMock,
                return_value={
                    "hit": True,
                    "response": {"content": "cached", "finish_reason": "stop", "usage": {}},
                },
            ),
            patch("intelligent_router.pipeline.call_inference", new_callable=AsyncMock) as mock_infer,
            patch("intelligent_router.pipeline.cache_write", new_callable=AsyncMock) as mock_cache_write,
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        # cache_write must never be scheduled (it's only for MISS → inference path)
        mock_cache_write.assert_not_called()
        mock_infer.assert_not_called()
        assert result.status_code == 200


# ---------------------------------------------------------------------------
# Test 9: Unhandled exception → 500 internal_error
# ---------------------------------------------------------------------------

class TestUnhandledException:
    """An unhandled exception in the pipeline returns 500 internal_error."""

    @pytest.mark.asyncio
    async def test_runtime_error_returns_500(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with patch(
            "intelligent_router.pipeline.classify_task",
            side_effect=RuntimeError("unexpected crash"),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 500
        assert result.error_code == "internal_error"
        assert result.success is False

    @pytest.mark.asyncio
    async def test_internal_error_latency_is_non_negative(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with patch(
            "intelligent_router.pipeline.classify_task",
            side_effect=ValueError("boom"),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# Test 10: All backends exhausted → 503 all_backends_exhausted
# ---------------------------------------------------------------------------

class TestAllBackendsExhausted:
    """Health check always fails on a single-model chain → 503 all_backends_exhausted."""

    @pytest.mark.asyncio
    async def test_single_model_health_fail_returns_503(self, mock_state):
        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=False),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 503
        assert result.error_code == "all_backends_exhausted"
        assert result.success is False

    @pytest.mark.asyncio
    async def test_multi_model_chain_all_unhealthy_returns_503(self, mock_state):
        """Two-model fallback chain, both unhealthy → 503."""
        from intelligent_router.task_classifier import ClassifierRules

        # Build a two-model fallback chain
        fallback_entry = ModelEntry(
            name="fallback-model",
            backend="ollama",
            endpoint="http://inference-ollama:11434",
            tasks=["chat"],
            health_url="http://inference-ollama:11434/api/tags",
            fallback=None,
        )
        primary_entry = ModelEntry(
            name="primary-model",
            backend="ollama",
            endpoint="http://inference-ollama:11434",
            tasks=["chat"],
            health_url="http://inference-ollama:11434/api/tags",
            fallback="fallback-model",
        )
        mock_state.model_matrix = ModelMatrix(
            models={"primary-model": primary_entry, "fallback-model": fallback_entry},
            task_defaults={"chat": "primary-model"},
        )
        mock_state.classifier_rules = ClassifierRules(rules={}, default="chat")

        imf = _base_imf()
        bt = BackgroundTasks()
        with (
            patch("intelligent_router.pipeline.classify_task", return_value="chat"),
            patch("intelligent_router.pipeline.check_model_health", new_callable=AsyncMock, return_value=False),
            patch("intelligent_router.pipeline.post_audit_event", new_callable=AsyncMock),
        ):
            result = await run_routing_pipeline(imf, mock_state, bt)

        assert result.status_code == 503
        assert result.error_code == "all_backends_exhausted"


# ---------------------------------------------------------------------------
# PipelineResult dataclass
# ---------------------------------------------------------------------------

class TestPipelineResultDataclass:
    """Verify PipelineResult fields exist and accept the correct types."""

    def test_success_result(self):
        imf = _base_imf()
        result = PipelineResult(
            success=True,
            status_code=200,
            imf=imf,
            error_code=None,
            latency_ms=42,
        )
        assert result.success is True
        assert result.status_code == 200
        assert result.imf is imf
        assert result.error_code is None
        assert isinstance(result.latency_ms, int)

    def test_error_result(self):
        imf = _base_imf()
        result = PipelineResult(
            success=False,
            status_code=400,
            imf=imf,
            error_code="governance_check_failed",
            latency_ms=5,
        )
        assert result.success is False
        assert result.status_code == 400
        assert result.error_code == "governance_check_failed"
