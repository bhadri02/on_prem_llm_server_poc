"""
Property-based tests for OpenAI compatibility in the Intelligent Router.

Properties covered:
  - Property 6: OpenAI Compatibility — Response Shape Invariant
    For any valid messages input, the /v1/chat/completions endpoint returns
    a response with all required OpenAI-compatible fields populated correctly.
    For 503 error responses, the error body follows the OpenAI error schema.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import copy
import json
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
from intelligent_router.policy import PolicyMatrix
from intelligent_router.main import create_app


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def valid_message_strategy():
    """Strategy for a single chat message dict."""
    return st.fixed_dictionaries({
        "role": st.sampled_from(["user", "assistant", "system"]),
        "content": st.text(min_size=1, max_size=200),
    })


# ---------------------------------------------------------------------------
# Test infrastructure: fresh app per Hypothesis example
# ---------------------------------------------------------------------------

def _make_router_app_and_state():
    """Build a test FastAPI app with state pre-populated directly.

    httpx.ASGITransport does not fire ASGI lifespan events, so we bypass
    the lifespan entirely and set app.state directly before returning the app.
    """
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

    app = create_app()
    # Remove the real lifespan so it does not interfere with test requests
    app.router.lifespan_context = None
    app.state.classifier_rules = rules
    app.state.model_matrix = matrix
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
    app.state.http_client = MagicMock()
    app.state.settings = mock_settings
    return app


# ---------------------------------------------------------------------------
# Property 6: OpenAI Response Shape Invariant
# ---------------------------------------------------------------------------

@given(
    messages=st.lists(valid_message_strategy(), min_size=1, max_size=10),
    model=st.one_of(st.none(), st.just("test-model")),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_openai_response_shape_invariant(messages, model):
    """**Validates: Requirements 9.2, 9.5**

    Property 6: OpenAI Compatibility — Response Shape Invariant.

    For any valid messages and optional model, POST /v1/chat/completions with
    a successful mocked Inference MUST return HTTP 200 with an OpenAI-compatible
    response body that has:
      - id: non-empty string
      - object == "chat.completion"
      - model: non-null string
      - choices[0].message.role == "assistant"
      - choices[0].message.content: non-null, non-empty string
      - choices[0].finish_reason: non-null string
      - usage.prompt_tokens >= 0
      - usage.completion_tokens >= 0
      - usage.total_tokens >= 0
    """

    async def _run():
        app = _make_router_app_and_state()
        transport = httpx.ASGITransport(app=app)

        # Build a valid inference response IMF
        def _make_inference_response(imf, *args, **kwargs):
            resp = copy.deepcopy(imf)
            resp["response"] = {
                "content": "This is a test response from the inference adapter.",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
            }
            return resp

        with (
            patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
            patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value={"hit": False})),
            patch("intelligent_router.pipeline.call_inference", new=AsyncMock(side_effect=_make_inference_response)),
            patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
            patch("intelligent_router.pipeline.cache_write", new=AsyncMock()),
        ):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                payload = {"messages": messages}
                if model is not None:
                    payload["model"] = model

                response = await client.post("/v1/chat/completions", json=payload)

        return response

    response = asyncio.run(_run())

    assert response.status_code == 200, (
        f"Expected HTTP 200, got {response.status_code}. Body: {response.text}"
    )

    body = response.json()

    # id: non-empty string
    assert "id" in body, "Response missing 'id' field"
    assert isinstance(body["id"], str) and len(body["id"]) > 0, (
        f"'id' should be a non-empty string, got {body['id']!r}"
    )

    # object == "chat.completion"
    assert body.get("object") == "chat.completion", (
        f"Expected object='chat.completion', got {body.get('object')!r}"
    )

    # model: non-null string
    assert body.get("model") is not None, "Response 'model' field is null"
    assert isinstance(body["model"], str), (
        f"'model' should be a string, got {body.get('model')!r}"
    )

    # choices array with at least one item
    assert "choices" in body and len(body["choices"]) > 0, (
        "Response missing 'choices' array or it is empty"
    )
    choice = body["choices"][0]

    # message.role == "assistant"
    assert choice.get("message", {}).get("role") == "assistant", (
        f"choices[0].message.role should be 'assistant', "
        f"got {choice.get('message', {}).get('role')!r}"
    )

    # message.content: non-null, non-empty string
    content = choice.get("message", {}).get("content")
    assert content is not None, "choices[0].message.content is null"
    assert isinstance(content, str) and len(content) > 0, (
        f"choices[0].message.content should be a non-empty string, got {content!r}"
    )

    # finish_reason: non-null string
    finish_reason = choice.get("finish_reason")
    assert finish_reason is not None, "choices[0].finish_reason is null"
    assert isinstance(finish_reason, str), (
        f"choices[0].finish_reason should be a string, got {finish_reason!r}"
    )

    # usage fields: non-negative integers
    usage = body.get("usage", {})
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert field in usage, f"Response 'usage' missing '{field}'"
        assert isinstance(usage[field], int) and usage[field] >= 0, (
            f"usage.{field} should be a non-negative int, got {usage[field]!r}"
        )


# ---------------------------------------------------------------------------
# Property 6 (error path): 503 error body follows OpenAI error schema
# ---------------------------------------------------------------------------

@given(
    messages=st.lists(valid_message_strategy(), min_size=1, max_size=5),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_openai_error_response_shape(messages):
    """**Validates: Requirements 9.5**

    When the pipeline returns a non-200 response (all backends exhausted),
    the error body MUST have error.code, error.message, error.type.
    """
    from intelligent_router.inference_client import InferenceError

    async def _run():
        app = _make_router_app_and_state()
        transport = httpx.ASGITransport(app=app)

        # Health check fails → all backends exhausted → 503
        with (
            patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=False)),
            patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
        ):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": messages},
                )

        return response

    response = asyncio.run(_run())

    assert response.status_code == 503, (
        f"Expected HTTP 503, got {response.status_code}. Body: {response.text}"
    )

    body = response.json()
    assert "error" in body, "503 response body missing 'error' field"
    error = body["error"]
    assert "code" in error, "error object missing 'code'"
    assert "message" in error, "error object missing 'message'"
    assert "type" in error, "error object missing 'type'"
