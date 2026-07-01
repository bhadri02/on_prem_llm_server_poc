"""
Unit tests for inference_adapter.services.ollama_client.OllamaClient.

Uses respx to intercept httpx calls so no real network is required.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from inference_adapter.services.ollama_client import (
    OllamaBackendError,
    OllamaClient,
    OllamaConnectionError,
    OllamaInvalidResponseError,
    OllamaRequestError,
    OllamaTimeoutError,
)

BASE_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# chat() — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_success_returns_parsed_json():
    """A 200 response with valid JSON is returned as a dict."""
    payload = {"message": {"role": "assistant", "content": "Hi!"}, "done": True}
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        result = await client.chat({"model": "llama3.2:3b", "messages": []})
        assert isinstance(result, dict)
        assert result["message"]["content"] == "Hi!"
        await client.close()


# ---------------------------------------------------------------------------
# chat() — error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_timeout_raises_ollama_timeout_error():
    """httpx.TimeoutException is re-raised as OllamaTimeoutError."""
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        client = OllamaClient(BASE_URL, timeout=1.0)
        with pytest.raises(OllamaTimeoutError):
            await client.chat({"model": "llama3.2:3b", "messages": []})
        await client.close()


@pytest.mark.asyncio
async def test_chat_connect_error_raises_ollama_connection_error():
    """httpx.ConnectError is re-raised as OllamaConnectionError."""
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            side_effect=httpx.ConnectError("refused")
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        with pytest.raises(OllamaConnectionError):
            await client.chat({"model": "llama3.2:3b", "messages": []})
        await client.close()


@pytest.mark.asyncio
async def test_chat_4xx_raises_ollama_request_error():
    """HTTP 422 response is raised as OllamaRequestError with correct status_code."""
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            return_value=httpx.Response(422, json={"error": "bad request"})
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        with pytest.raises(OllamaRequestError) as exc_info:
            await client.chat({"model": "llama3.2:3b", "messages": []})
        assert exc_info.value.status_code == 422
        await client.close()


@pytest.mark.asyncio
async def test_chat_5xx_raises_ollama_backend_error():
    """HTTP 500 response is raised as OllamaBackendError with correct status_code."""
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            return_value=httpx.Response(500, text="internal server error")
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        with pytest.raises(OllamaBackendError) as exc_info:
            await client.chat({"model": "llama3.2:3b", "messages": []})
        assert exc_info.value.status_code == 500
        await client.close()


@pytest.mark.asyncio
async def test_chat_invalid_json_raises_ollama_invalid_response_error():
    """A 200 response with non-JSON body is raised as OllamaInvalidResponseError."""
    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(
            return_value=httpx.Response(200, text="not json <<<")
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        with pytest.raises(OllamaInvalidResponseError):
            await client.chat({"model": "llama3.2:3b", "messages": []})
        await client.close()


# ---------------------------------------------------------------------------
# chat() — stream forced to False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_forces_stream_false():
    """stream is always False in the request body, regardless of caller input."""
    captured_body: dict = {}

    def capture_and_respond(request: httpx.Request):
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

    with respx.mock:
        respx.post(f"{BASE_URL}/api/chat").mock(side_effect=capture_and_respond)
        client = OllamaClient(BASE_URL, timeout=10.0)
        # Caller passes stream=True — should be forced to False
        await client.chat({"model": "llama3.2:3b", "messages": [], "stream": True})
        assert captured_body.get("stream") is False
        await client.close()


# ---------------------------------------------------------------------------
# list_models()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_models_parses_names_correctly():
    """list_models() extracts the 'name' from each entry in models array."""
    tags_response = {
        "models": [
            {"name": "llama3.2:3b", "size": 12345},
        ]
    }
    with respx.mock:
        respx.get(f"{BASE_URL}/api/tags").mock(
            return_value=httpx.Response(200, json=tags_response)
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        models = await client.list_models()
        assert models == ["llama3.2:3b"]
        await client.close()


@pytest.mark.asyncio
async def test_list_models_returns_empty_list_when_models_absent():
    """list_models() returns [] when the 'models' key is absent."""
    with respx.mock:
        respx.get(f"{BASE_URL}/api/tags").mock(
            return_value=httpx.Response(200, json={})
        )
        client = OllamaClient(BASE_URL, timeout=10.0)
        models = await client.list_models()
        assert models == []
        await client.close()


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_closes_client():
    """After close(), the underlying httpx.AsyncClient is closed."""
    with respx.mock:
        client = OllamaClient(BASE_URL, timeout=10.0)
        assert not client._client.is_closed
        await client.close()
        assert client._client.is_closed
