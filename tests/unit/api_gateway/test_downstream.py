"""
Unit tests for api_gateway/services/downstream.py — forward_to_security().

Validates: Requirements 5.1–5.5
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api_gateway.schemas.imf import IMFDocument, IMFRequest
from api_gateway.services.downstream import DownstreamError, forward_to_security

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_REQUEST_ID = str(uuid.uuid4())

_VALID_IMF = IMFDocument(
    request_id=_VALID_REQUEST_ID,
    trace_id=_VALID_REQUEST_ID,
    timestamp_utc="2024-01-01T00:00:00Z",
    request=IMFRequest(model="gpt-4"),
)


def _make_mock_response(status_code: int, body: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    if body is not None:
        raw = json.dumps(body).encode()
        mock_resp.content = raw
        mock_resp.json.return_value = body
    else:
        mock_resp.content = b""
        mock_resp.json.side_effect = ValueError("no content")
    return mock_resp


def _make_client(response: httpx.Response | Exception) -> MagicMock:
    """Build a mock AsyncClient whose .post() returns the given response."""
    client = MagicMock(spec=httpx.AsyncClient)
    if isinstance(response, Exception):
        client.post = AsyncMock(side_effect=response)
    else:
        client.post = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Success path  (Req 5.1, 5.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_settings(monkeypatch):
    """Ensure get_settings() returns predictable values."""
    import api_gateway.services.downstream as ds_module
    settings_mock = MagicMock()
    settings_mock.downstream_security_url = "http://security-layer:8081"
    settings_mock.downstream_timeout_seconds = 10.0
    monkeypatch.setattr(ds_module, "get_settings", lambda: settings_mock)
    return settings_mock


@pytest.mark.asyncio
async def test_successful_200_returns_imf_document(patched_settings):
    """Req 5.1, 5.2: 200 response with valid IMF JSON → returns IMFDocument."""
    body = _VALID_IMF.model_dump()
    client = _make_client(_make_mock_response(200, body))
    result = await forward_to_security(_VALID_IMF, client)
    assert isinstance(result, IMFDocument)
    assert result.request_id == _VALID_REQUEST_ID


@pytest.mark.asyncio
async def test_post_is_called_with_correct_url(patched_settings):
    """Req 5.1: posts to {downstream_security_url}/process."""
    body = _VALID_IMF.model_dump()
    client = _make_client(_make_mock_response(200, body))
    await forward_to_security(_VALID_IMF, client)
    call_args = client.post.call_args
    assert call_args[0][0] == "http://security-layer:8081/process"


@pytest.mark.asyncio
async def test_post_uses_correct_content_type_header(patched_settings):
    """Req 5.1: Content-Type header must be application/json."""
    body = _VALID_IMF.model_dump()
    client = _make_client(_make_mock_response(200, body))
    await forward_to_security(_VALID_IMF, client)
    call_kwargs = client.post.call_args[1]
    assert call_kwargs["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Non-200 responses  (Req 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 500, 502, 503])
async def test_non_200_raises_downstream_error_502(status_code, patched_settings):
    """Req 5.3: any non-200 status → DownstreamError(502)."""
    client = _make_client(_make_mock_response(status_code, {"error": "oops"}))
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# Network / connection errors  (Req 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_exception_raises_downstream_error_502(patched_settings):
    """Req 5.4: TimeoutException → DownstreamError(502)."""
    client = _make_client(httpx.TimeoutException("timed out"))
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_connect_error_raises_downstream_error_502(patched_settings):
    """Req 5.4: ConnectError → DownstreamError(502)."""
    client = _make_client(httpx.ConnectError("connection refused"))
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_request_error_raises_downstream_error_502(patched_settings):
    """Req 5.4: generic RequestError → DownstreamError(502)."""
    client = _make_client(httpx.RequestError("dns failure"))
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# Empty or non-JSON body on 200  (Req 5.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_body_on_200_raises_downstream_error_502(patched_settings):
    """Req 5.5: 200 with empty body → DownstreamError(502)."""
    client = _make_client(_make_mock_response(200, None))
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_non_json_body_on_200_raises_downstream_error_502(patched_settings):
    """Req 5.5: 200 with non-JSON body → DownstreamError(502)."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.content = b"not json at all"
    mock_resp.json.side_effect = ValueError("not json")
    client = _make_client(mock_resp)
    with pytest.raises(DownstreamError) as exc_info:
        await forward_to_security(_VALID_IMF, client)
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# DownstreamError attributes
# ---------------------------------------------------------------------------


def test_downstream_error_stores_status_code():
    err = DownstreamError(502)
    assert err.status_code == 502


def test_downstream_error_is_exception():
    err = DownstreamError(502)
    assert isinstance(err, Exception)
