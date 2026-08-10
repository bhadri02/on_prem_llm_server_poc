"""
Integration tests for POST /infer's cloud-backend dispatch path
(routing.backend == "anthropic").

Uses the same app_client / valid_imf_doc fixtures as the Ollama tests, but
sets routing.backend = "anthropic" and drives mock_anthropic_client /
mock_registry_http_client instead of mock_ollama_client.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from inference_adapter.services.anthropic_client import (
    AnthropicBackendError,
    AnthropicConnectionError,
    AnthropicInvalidResponseError,
    AnthropicRequestError,
    AnthropicTimeoutError,
)
from inference_adapter.services.model_secret_resolver import ModelSecretUnavailable


def _anthropic_imf(valid_imf_doc: dict) -> dict:
    doc = copy.deepcopy(valid_imf_doc)
    doc["routing"]["selected_model"] = "claude-sonnet-5"
    doc["request"]["model"] = "claude-sonnet-5"
    doc["routing"]["backend"] = "anthropic"
    return doc


def _registry_response(status_code: int, json_body: dict) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=json_body)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_happy_path_returns_200(app_client, valid_imf_doc, mock_registry_http_client):
    """A well-formed request with routing.backend="anthropic" and a key on
    file in the registry returns 200 with the mapped IMF response."""
    mock_registry_http_client.get = AsyncMock(
        return_value=_registry_response(200, {"api_key": "sk-ant-secret"})
    )

    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 200

    body = response.json()
    assert body["response"]["content"] == "Hello from Claude!"
    assert body["response"]["finish_reason"] == "stop"
    assert body["response"]["usage"]["prompt_tokens"] == 12
    assert body["response"]["usage"]["completion_tokens"] == 7
    assert body["response"]["usage"]["total_tokens"] == 19
    assert body["metadata"]["inference_backend"] == "anthropic"


@pytest.mark.asyncio
async def test_anthropic_dispatch_never_touches_ollama_models_list(
    app_client, valid_imf_doc, mock_registry_http_client, mock_ollama_client
):
    """The ollama_models membership check must not apply to cloud models —
    "claude-sonnet-5" is never in app.state.ollama_models."""
    mock_registry_http_client.get = AsyncMock(
        return_value=_registry_response(200, {"api_key": "sk-ant-secret"})
    )
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 200
    mock_ollama_client.chat.assert_not_called()


# ---------------------------------------------------------------------------
# Backward compatibility — absent/None routing.backend still means "ollama"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_backend_field_defaults_to_ollama(app_client, valid_imf_doc, mock_anthropic_client):
    """routing.backend absent (every pre-existing caller) must still take
    the Ollama path unchanged — this is the whole point of defaulting to
    "ollama" rather than requiring every caller to set the new field."""
    assert "backend" not in valid_imf_doc["routing"]
    response = await app_client.post("/infer", json=valid_imf_doc)
    assert response.status_code == 200
    assert response.json()["metadata"]["inference_backend"] == "ollama"
    mock_anthropic_client.messages.assert_not_called()


# ---------------------------------------------------------------------------
# Model Registry secret resolution failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_unreachable_returns_503(app_client, valid_imf_doc, mock_registry_http_client):
    mock_registry_http_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 503
    assert response.json()["event"] == "model_registry_unreachable"


@pytest.mark.asyncio
async def test_no_api_key_on_file_returns_422(app_client, valid_imf_doc, mock_registry_http_client):
    """Model registered but api_key never set (api_key: null) → 422, distinct
    from a registry-unreachable 503."""
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": None}))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 422
    assert response.json()["event"] == "provider_api_key_not_configured"


@pytest.mark.asyncio
async def test_model_not_registered_returns_422_not_configured(app_client, valid_imf_doc, mock_registry_http_client):
    """404 from the registry (model never registered there at all) is
    treated the same as "no key configured" — there's nothing to dispatch
    with either way."""
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(404, {}))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 422
    assert response.json()["event"] == "provider_api_key_not_configured"


# ---------------------------------------------------------------------------
# Anthropic error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_timeout_returns_503(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(side_effect=AnthropicTimeoutError("timed out"))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 503
    assert response.json()["event"] == "anthropic_unreachable"


@pytest.mark.asyncio
async def test_anthropic_connection_error_returns_503(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(side_effect=AnthropicConnectionError("refused"))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 503
    assert response.json()["event"] == "anthropic_unreachable"


@pytest.mark.asyncio
async def test_anthropic_4xx_returns_422_request_rejected(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    """Covers a bad/revoked key (401) just as much as a malformed request —
    both are "the request as sent was rejected"."""
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(side_effect=AnthropicRequestError(401))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 422
    assert response.json()["event"] == "anthropic_request_rejected"


@pytest.mark.asyncio
async def test_anthropic_5xx_returns_502_backend_error(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(side_effect=AnthropicBackendError(529))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 502
    assert response.json()["event"] == "anthropic_backend_error"


@pytest.mark.asyncio
async def test_anthropic_invalid_response_returns_502(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(side_effect=AnthropicInvalidResponseError("bad json"))
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 502
    assert response.json()["event"] == "anthropic_invalid_response"


@pytest.mark.asyncio
async def test_anthropic_empty_content_array_returns_502(app_client, valid_imf_doc, mock_registry_http_client, mock_anthropic_client):
    """Anthropic returning a response with no text content block is treated
    as an invalid response, same status/event as unparseable JSON."""
    mock_registry_http_client.get = AsyncMock(return_value=_registry_response(200, {"api_key": "sk-ant-secret"}))
    mock_anthropic_client.messages = AsyncMock(return_value={"content": [], "stop_reason": "end_turn", "usage": {}})
    response = await app_client.post("/infer", json=_anthropic_imf(valid_imf_doc))
    assert response.status_code == 502
    assert response.json()["event"] == "anthropic_invalid_response"


# ---------------------------------------------------------------------------
# Unsupported backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrecognised_backend_returns_422(app_client, valid_imf_doc):
    doc = copy.deepcopy(valid_imf_doc)
    doc["routing"]["backend"] = "some-future-provider"
    response = await app_client.post("/infer", json=doc)
    assert response.status_code == 422
    assert response.json()["event"] == "unsupported_backend"
