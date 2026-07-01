"""
Integration tests for POST /infer endpoint (inference_adapter.routers.infer).

Uses the app_client and valid_imf_doc fixtures from conftest.py.
Error-path tests override mock_ollama_client.chat via app.state to simulate
specific Ollama failure modes.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest

from inference_adapter.main import app
from inference_adapter.services.ollama_client import (
    OllamaBackendError,
    OllamaConnectionError,
    OllamaInvalidResponseError,
    OllamaRequestError,
    OllamaTimeoutError,
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_imf_returns_200_with_populated_response(app_client, valid_imf_doc):
    """A well-formed IMF request returns 200 with all expected response fields."""
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 200

    body = response.json()
    assert body["response"]["content"] == "Hello, world!"
    assert body["response"]["finish_reason"] == "stop"
    assert body["response"]["usage"]["prompt_tokens"] == 10
    assert body["response"]["usage"]["completion_tokens"] == 5
    assert body["response"]["usage"]["total_tokens"] == 15
    assert body["metadata"]["inference_backend"] == "ollama"


# ---------------------------------------------------------------------------
# Validation errors — 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_selected_model_returns_422(app_client, valid_imf_doc):
    """Absent routing.selected_model must return 422 with event=missing_selected_model."""
    doc = copy.deepcopy(valid_imf_doc)
    doc["routing"]["selected_model"] = None

    response = await app_client.post("/infer", json=doc)
    assert response.status_code == 422
    assert response.json()["event"] == "missing_selected_model"


@pytest.mark.asyncio
async def test_empty_messages_returns_422_event_empty_messages(app_client, valid_imf_doc):
    """Empty messages list must return 422 with event=empty_messages."""
    doc = copy.deepcopy(valid_imf_doc)
    doc["request"]["messages"] = []

    response = await app_client.post("/infer", json=doc)
    assert response.status_code == 422
    assert response.json()["event"] == "empty_messages"


@pytest.mark.asyncio
async def test_model_not_in_list_returns_422_event_model_not_loaded(app_client, valid_imf_doc):
    """A selected_model not in the loaded model list returns 422 with event=model_not_loaded."""
    doc = copy.deepcopy(valid_imf_doc)
    doc["routing"]["selected_model"] = "gpt-99:unknown"
    doc["request"]["model"] = "gpt-99:unknown"

    response = await app_client.post("/infer", json=doc)
    assert response.status_code == 422
    assert response.json()["event"] == "model_not_loaded"


# ---------------------------------------------------------------------------
# Ollama error mapping — 503
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_timeout_returns_503_event_ollama_unreachable(app_client, valid_imf_doc):
    """OllamaTimeoutError from chat() → 503 with event=ollama_unreachable."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=OllamaTimeoutError("timed out")
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 503
    assert response.json()["event"] == "ollama_unreachable"


@pytest.mark.asyncio
async def test_ollama_connection_error_returns_503(app_client, valid_imf_doc):
    """OllamaConnectionError from chat() → 503 with event=ollama_unreachable."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=OllamaConnectionError("refused")
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 503
    assert response.json()["event"] == "ollama_unreachable"


# ---------------------------------------------------------------------------
# Ollama error mapping — 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_4xx_returns_422_event_ollama_request_rejected(app_client, valid_imf_doc):
    """OllamaRequestError from chat() → 422 with event=ollama_request_rejected."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=OllamaRequestError(422)
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 422
    assert response.json()["event"] == "ollama_request_rejected"


# ---------------------------------------------------------------------------
# Ollama error mapping — 502
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_5xx_returns_502_event_ollama_backend_error(app_client, valid_imf_doc):
    """OllamaBackendError from chat() → 502 with event=ollama_backend_error."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=OllamaBackendError(500)
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 502
    assert response.json()["event"] == "ollama_backend_error"


@pytest.mark.asyncio
async def test_ollama_invalid_json_returns_502_event_ollama_invalid_response(
    app_client, valid_imf_doc
):
    """OllamaInvalidResponseError from chat() → 502 with event=ollama_invalid_response."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=OllamaInvalidResponseError("bad json")
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 502
    assert response.json()["event"] == "ollama_invalid_response"


# ---------------------------------------------------------------------------
# Unhandled exception — 500
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unhandled_exception_returns_500_event_internal_error(app_client, valid_imf_doc):
    """An unexpected exception from chat() → 500 with event=internal_error."""
    app.state.ollama_client.chat = AsyncMock(
        side_effect=RuntimeError("something unexpected")
    )
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 500
    assert response.json()["event"] == "internal_error"


# ---------------------------------------------------------------------------
# Streaming guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_true_logs_warning_and_proceeds_non_streaming(
    app_client, valid_imf_doc
):
    """
    A request with stream=True must not fail; the endpoint proceeds with
    stream=False and returns 200.
    """
    doc = copy.deepcopy(valid_imf_doc)
    doc["request"]["stream"] = True

    # Reset to the default successful mock
    from unittest.mock import AsyncMock as _AM

    app.state.ollama_client.chat = _AM(
        return_value={
            "message": {"role": "assistant", "content": "Hello, world!"},
            "done_reason": "stop",
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
            "total_duration": 1_500_000_000,
        }
    )

    response = await app_client.post("/infer", json=doc)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Content-Type on all error responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_error_responses_have_content_type_application_json(
    app_client, valid_imf_doc
):
    """Every error response must have Content-Type: application/json."""
    error_cases = [
        # (doc_mutator, side_effect)
        (lambda d: d["routing"].__setitem__("selected_model", None), None),
        (lambda d: d["request"].__setitem__("messages", []), None),
    ]

    for mutator, _ in error_cases:
        doc = copy.deepcopy(valid_imf_doc)
        mutator(doc)
        response = await app_client.post("/infer", json=doc)
        assert "application/json" in response.headers.get("content-type", ""), (
            f"Expected application/json, got {response.headers.get('content-type')}"
        )

    # Also check Ollama error responses
    ollama_errors = [
        OllamaTimeoutError("t"),
        OllamaConnectionError("c"),
        OllamaRequestError(422),
        OllamaBackendError(500),
        OllamaInvalidResponseError("i"),
    ]
    for err in ollama_errors:
        app.state.ollama_client.chat = AsyncMock(side_effect=err)
        response = await app_client.post("/infer", json=valid_imf_doc)
        assert "application/json" in response.headers.get("content-type", ""), (
            f"Expected application/json for {type(err).__name__}, "
            f"got {response.headers.get('content-type')}"
        )


# ---------------------------------------------------------------------------
# event and request_id keys present on all error responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_error_responses_contain_event_and_request_id_keys(
    app_client, valid_imf_doc
):
    """Every error response body must contain 'event' and 'request_id' keys."""
    # 422 — missing selected_model
    doc = copy.deepcopy(valid_imf_doc)
    doc["routing"]["selected_model"] = None
    body = (await app_client.post("/infer", json=doc)).json()
    assert "event" in body
    assert "request_id" in body

    # 422 — empty messages
    doc = copy.deepcopy(valid_imf_doc)
    doc["request"]["messages"] = []
    body = (await app_client.post("/infer", json=doc)).json()
    assert "event" in body
    assert "request_id" in body

    # 503 — timeout
    app.state.ollama_client.chat = AsyncMock(side_effect=OllamaTimeoutError("t"))
    body = (await app_client.post("/infer", json=valid_imf_doc)).json()
    assert "event" in body
    assert "request_id" in body

    # 502 — backend error
    app.state.ollama_client.chat = AsyncMock(side_effect=OllamaBackendError(500))
    body = (await app_client.post("/infer", json=valid_imf_doc)).json()
    assert "event" in body
    assert "request_id" in body

    # 500 — internal error
    app.state.ollama_client.chat = AsyncMock(side_effect=RuntimeError("boom"))
    body = (await app_client.post("/infer", json=valid_imf_doc)).json()
    assert "event" in body
    assert "request_id" in body


# ---------------------------------------------------------------------------
# No partial response block in error responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_responses_contain_no_partial_response_block(
    app_client, valid_imf_doc
):
    """
    Error responses must not include a 'response' key with partial data —
    they should be plain error envelopes.
    """
    error_setups = [
        (OllamaTimeoutError("t"), 503),
        (OllamaConnectionError("c"), 503),
        (OllamaRequestError(422), 422),
        (OllamaBackendError(500), 502),
        (OllamaInvalidResponseError("i"), 502),
        (RuntimeError("x"), 500),
    ]

    for exc, expected_status in error_setups:
        app.state.ollama_client.chat = AsyncMock(side_effect=exc)
        response = await app_client.post("/infer", json=valid_imf_doc)
        assert response.status_code == expected_status
        body = response.json()
        # Error envelopes should not carry a 'response' key
        assert "response" not in body, (
            f"Error response for {type(exc).__name__} unexpectedly contained "
            f"a 'response' key: {body}"
        )
