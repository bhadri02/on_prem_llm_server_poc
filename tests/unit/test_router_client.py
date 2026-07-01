"""
Unit tests for security_layer/router_client.py.

Uses pytest-httpx (httpx_mock fixture) to mock the httpx transport layer.
All tests are async and use @pytest.mark.anyio (the project's async backend).

Covers:
- httpx.TimeoutException  → RouterTimeoutError
- httpx.ConnectError      → RouterUnavailableError
- 2xx + valid JSON        → (status_code, dict)
- 2xx + empty body        → RouterInvalidResponseError
- 2xx + non-JSON body     → RouterInvalidResponseError
- non-2xx (e.g. 500)      → (status_code, body_dict) — no raise
- X-Request-Id header set to request_id on every POST
"""

import json

import httpx
import pytest

from security_layer.router_client import (
    RouterInvalidResponseError,
    RouterTimeoutError,
    RouterUnavailableError,
    forward_to_router,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROUTER_URL = "http://mock-router:8082"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_IMF = {"request_id": REQUEST_ID, "request": {"messages": [{"role": "user", "content": "hi"}]}}
ROUTE_URL = f"{ROUTER_URL}/router/route"


# ---------------------------------------------------------------------------
# Timeout → RouterTimeoutError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_exception_raises_router_timeout_error(httpx_mock):
    """httpx.TimeoutException must be converted to RouterTimeoutError."""
    httpx_mock.add_exception(httpx.TimeoutException("timeout"))
    with pytest.raises(RouterTimeoutError):
        await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)


# ---------------------------------------------------------------------------
# ConnectError → RouterUnavailableError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_connect_error_raises_router_unavailable_error(httpx_mock):
    """httpx.ConnectError must be converted to RouterUnavailableError."""
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(RouterUnavailableError):
        await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)


# ---------------------------------------------------------------------------
# 2xx + valid JSON → (status_code, dict)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_2xx_valid_json_returns_status_and_body(httpx_mock):
    """2xx response with valid JSON body returns (status_code, body_dict)."""
    body = {"request_id": REQUEST_ID, "response": {"content": "Hello"}}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    status, resp_body = await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)
    assert status == 200
    assert resp_body == body


@pytest.mark.anyio
async def test_201_valid_json_returns_status_and_body(httpx_mock):
    """201 is also a 2xx — should return (201, body_dict)."""
    body = {"created": True}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=201,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    status, resp_body = await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)
    assert status == 201
    assert resp_body == body


# ---------------------------------------------------------------------------
# 2xx + empty body → RouterInvalidResponseError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_2xx_empty_body_raises_router_invalid_response_error(httpx_mock):
    """2xx with empty bytes body must raise RouterInvalidResponseError."""
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=b"",
        headers={"content-type": "application/json"},
    )
    with pytest.raises(RouterInvalidResponseError):
        await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)


# ---------------------------------------------------------------------------
# 2xx + non-JSON body → RouterInvalidResponseError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_2xx_non_json_body_raises_router_invalid_response_error(httpx_mock):
    """2xx with non-JSON body must raise RouterInvalidResponseError."""
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=b"not json",
        headers={"content-type": "text/plain"},
    )
    with pytest.raises(RouterInvalidResponseError):
        await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)


@pytest.mark.anyio
async def test_2xx_partial_json_raises_router_invalid_response_error(httpx_mock):
    """2xx with partial/broken JSON must raise RouterInvalidResponseError."""
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=b'{"key": ',
        headers={"content-type": "application/json"},
    )
    with pytest.raises(RouterInvalidResponseError):
        await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)


# ---------------------------------------------------------------------------
# Non-2xx → (status_code, body_dict) — no raise
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_500_returns_status_and_body_without_raising(httpx_mock):
    """Non-2xx (500) must return (status_code, body_dict) without raising."""
    body = {"error": "internal_server_error"}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=500,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    status, resp_body = await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)
    assert status == 500
    assert resp_body == body


@pytest.mark.anyio
async def test_400_returns_status_and_body_without_raising(httpx_mock):
    """Non-2xx (400) must return (status_code, body_dict) without raising."""
    body = {"error": "bad_request"}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=400,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    status, resp_body = await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)
    assert status == 400
    assert resp_body == body


@pytest.mark.anyio
async def test_503_returns_status_and_body_without_raising(httpx_mock):
    """Non-2xx (503) must return (status_code, body_dict) without raising."""
    body = {"error": "service_unavailable"}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=503,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    status, resp_body = await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)
    assert status == 503
    assert resp_body == body


# ---------------------------------------------------------------------------
# X-Request-Id header is sent on every POST
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_x_request_id_header_is_set_on_2xx_request(httpx_mock):
    """The X-Request-Id header must equal request_id in every POST."""
    body = {"response": {"content": "ok"}}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers.get("x-request-id") == REQUEST_ID


@pytest.mark.anyio
async def test_x_request_id_header_is_set_on_non_2xx_request(httpx_mock):
    """X-Request-Id must be present even when the router returns a non-2xx."""
    body = {"error": "bad"}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=500,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    await forward_to_router(SAMPLE_IMF, ROUTER_URL, REQUEST_ID)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers.get("x-request-id") == REQUEST_ID


@pytest.mark.anyio
async def test_x_request_id_header_matches_custom_request_id(httpx_mock):
    """X-Request-Id header value must always equal the passed request_id argument."""
    custom_id = "123e4567-e89b-42d3-a456-426614174000"
    body = {"response": {"content": "ok"}}
    httpx_mock.add_response(
        method="POST",
        url=ROUTE_URL,
        status_code=200,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    await forward_to_router(SAMPLE_IMF, ROUTER_URL, custom_id)

    requests = httpx_mock.get_requests()
    assert requests[0].headers.get("x-request-id") == custom_id
