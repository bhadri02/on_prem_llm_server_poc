"""
tests/inference_adapter/test_infer_stream_router.py

Tests for POST /infer/stream — the streaming counterpart to POST /infer.

Wire protocol under test (see inference_adapter/routers/infer.py's module
docstring): newline-delimited JSON, {"type": "delta"|"done"|"error", ...}.
HTTP status is always 200; failures are signaled in-band via a single
"error" line so callers must inspect stream content, not just status code.
"""

from __future__ import annotations

import json

import httpx
import pytest

from inference_adapter.services.ollama_client import (
    OllamaConnectionError,
    OllamaTimeoutError,
)


async def _read_ndjson_lines(response) -> list[dict]:
    lines = []
    async for raw_line in response.aiter_lines():
        if raw_line.strip():
            lines.append(json.loads(raw_line))
    return lines


# ---------------------------------------------------------------------------
# Ollama backend — happy path
# ---------------------------------------------------------------------------


async def _fake_ollama_stream_success(payload):
    yield {"message": {"content": "Hello"}, "done": False}
    yield {"message": {"content": ", world!"}, "done": False}
    yield {
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
        "total_duration": 1_500_000_000,
    }


class TestOllamaStreamSuccess:
    async def test_deltas_then_done_with_full_content(self, app_client, mock_ollama_client, valid_imf_doc):
        mock_ollama_client.chat_stream = _fake_ollama_stream_success

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            assert response.status_code == 200
            lines = await _read_ndjson_lines(response)

        deltas = [l for l in lines if l["type"] == "delta"]
        done = [l for l in lines if l["type"] == "done"]

        assert [d["content"] for d in deltas] == ["Hello", ", world!"]
        assert len(done) == 1
        assert done[0]["imf"]["response"]["content"] == "Hello, world!"
        assert done[0]["imf"]["response"]["finish_reason"] == "stop"
        assert done[0]["imf"]["response"]["usage"]["prompt_tokens"] == 10
        assert done[0]["imf"]["response"]["usage"]["completion_tokens"] == 5

    async def test_no_error_line_on_success(self, app_client, mock_ollama_client, valid_imf_doc):
        mock_ollama_client.chat_stream = _fake_ollama_stream_success

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            lines = await _read_ndjson_lines(response)

        assert not any(l["type"] == "error" for l in lines)


# ---------------------------------------------------------------------------
# Pre-flight validation — same event codes as /infer, delivered in-band
# ---------------------------------------------------------------------------


class TestPreflightValidation:
    async def test_missing_selected_model_returns_error_line(self, app_client, valid_imf_doc):
        valid_imf_doc["routing"]["selected_model"] = None

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            assert response.status_code == 200
            lines = await _read_ndjson_lines(response)

        assert len(lines) == 1
        assert lines[0] == {
            "type": "error",
            "event": "missing_selected_model",
            "status_code": 422,
            "request_id": valid_imf_doc["request_id"],
        }

    async def test_empty_messages_returns_error_line(self, app_client, valid_imf_doc):
        valid_imf_doc["request"]["messages"] = []

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            lines = await _read_ndjson_lines(response)

        assert len(lines) == 1
        assert lines[0]["type"] == "error"
        assert lines[0]["event"] == "empty_messages"
        assert lines[0]["status_code"] == 422

    async def test_model_not_loaded_returns_error_line(self, app_client, valid_imf_doc):
        valid_imf_doc["routing"]["selected_model"] = "not-a-real-model"

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            lines = await _read_ndjson_lines(response)

        assert len(lines) == 1
        assert lines[0]["event"] == "model_not_loaded"
        assert lines[0]["status_code"] == 422


# ---------------------------------------------------------------------------
# Ollama backend — mid-stream failure
# ---------------------------------------------------------------------------


class TestOllamaStreamFailure:
    async def test_timeout_before_any_delta_yields_error_line(self, app_client, mock_ollama_client, valid_imf_doc):
        async def _raise_timeout(payload):
            raise OllamaTimeoutError("boom")
            yield  # pragma: no cover — makes this a generator function

        mock_ollama_client.chat_stream = _raise_timeout

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            lines = await _read_ndjson_lines(response)

        assert len(lines) == 1
        assert lines[0]["type"] == "error"
        assert lines[0]["event"] == "ollama_unreachable"
        assert lines[0]["status_code"] == 503

    async def test_connection_error_mid_stream_after_partial_deltas(self, app_client, mock_ollama_client, valid_imf_doc):
        """A failure after some deltas already flushed still ends with a
        clear error line — the client must be prepared to discard a
        partial response, not just append the error text as content."""

        async def _partial_then_fail(payload):
            yield {"message": {"content": "Once upon"}, "done": False}
            raise OllamaConnectionError("connection reset")

        mock_ollama_client.chat_stream = _partial_then_fail

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            lines = await _read_ndjson_lines(response)

        assert lines[0] == {"type": "delta", "content": "Once upon"}
        assert lines[-1]["type"] == "error"
        assert lines[-1]["event"] == "ollama_unreachable"


# ---------------------------------------------------------------------------
# Anthropic backend — happy path
# ---------------------------------------------------------------------------


async def _fake_anthropic_stream_success(payload, api_key):
    yield {"type": "content_delta", "text": "Hi"}
    yield {"type": "content_delta", "text": " there"}
    yield {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 8, "output_tokens": 3}}


class TestAnthropicStreamSuccess:
    async def test_deltas_then_done(
        self, app_client, mock_anthropic_client, mock_registry_http_client, valid_imf_doc
    ):
        valid_imf_doc["routing"]["backend"] = "anthropic"
        valid_imf_doc["routing"]["selected_model"] = "claude-sonnet-4-5"
        mock_anthropic_client.messages_stream = _fake_anthropic_stream_success
        mock_registry_http_client.get.return_value = httpx.Response(
            status_code=200, json={"api_key": "sk-ant-test"}
        )

        async with app_client.stream("POST", "/infer/stream", json=valid_imf_doc) as response:
            assert response.status_code == 200
            lines = await _read_ndjson_lines(response)

        deltas = [l for l in lines if l["type"] == "delta"]
        done = [l for l in lines if l["type"] == "done"]

        assert [d["content"] for d in deltas] == ["Hi", " there"]
        assert len(done) == 1
        assert done[0]["imf"]["response"]["content"] == "Hi there"
        assert done[0]["imf"]["response"]["finish_reason"] == "stop"
