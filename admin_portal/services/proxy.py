"""
admin_portal/services/proxy.py

Generic async HTTP proxy helper for the Admin/Developer Portal (Layer 10).

Provides a single coroutine, ``async_proxy``, that forwards an HTTP request
to an upstream service using a shared ``httpx.AsyncClient``.  Network-level
failures (connection refused, DNS failure, timeout) are converted into a
``ProxyUnavailableError`` that callers translate into an HTTP 502 response.

Usage
-----
    from admin_portal.services.proxy import async_proxy, ProxyUnavailableError

    try:
        response = await async_proxy(
            client, "POST", "http://api-gateway:8080/v1/chat/completions",
            headers={"X-API-Key": settings.GATEWAY_API_KEY},
            json=body,
            timeout=30.0,
        )
    except ProxyUnavailableError as exc:
        raise HTTPException(502, detail=ErrorResponse(
            error="upstream_unavailable",
            message=str(exc),
            upstream="api-gateway",
        ).model_dump())

Validates: Requirements 3.2, 3.3, 3.4, 4.6, 5.7, 6.4, 7.7, 8.3
"""

from __future__ import annotations

# Aliased because sse_relay_with_inband_error below has a `json` parameter
# (the outgoing request body) that would otherwise shadow the module.
import json as _json_module
from typing import Any, AsyncIterator, Dict, Optional

import httpx


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class ProxyUnavailableError(Exception):
    """Raised when an upstream service is unreachable or times out.

    Attributes
    ----------
    upstream_name : str
        Human-readable name of the unreachable upstream (e.g. ``"api-gateway"``).
    """

    def __init__(self, upstream_name: str) -> None:
        self.upstream_name = upstream_name
        super().__init__(f"{upstream_name} is unreachable or timed out")


# ---------------------------------------------------------------------------
# Core proxy coroutine
# ---------------------------------------------------------------------------

async def async_proxy(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    timeout: float,
) -> httpx.Response:
    """Forward an HTTP request to ``url`` and return the raw ``httpx.Response``.

    Parameters
    ----------
    client:
        A live ``httpx.AsyncClient`` instance (caller owns its lifecycle).
    method:
        HTTP method string, e.g. ``"GET"``, ``"POST"``, ``"PATCH"``.
    url:
        Fully-qualified target URL including scheme, host, and path.
    headers:
        Optional additional request headers.  Merged with any headers already
        present on ``client``.
    json:
        Optional Python object to serialise as the JSON request body.
    timeout:
        Per-request timeout in seconds.  Applies to the entire round-trip
        (connect + read).

    Returns
    -------
    httpx.Response
        The upstream response, **not** yet consumed — callers read
        ``.content``, ``.json()``, or ``.text`` themselves.

    Raises
    ------
    ProxyUnavailableError
        On ``httpx.ConnectError`` or ``httpx.TimeoutException``.  All other
        httpx exceptions propagate unchanged.
    """
    try:
        response = await client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        )
        return response
    except httpx.ConnectError as exc:
        # Extract a readable upstream name from the URL if caller doesn't
        # wrap this themselves — but we have no ``upstream_name`` argument here,
        # so re-raise as a generic label.  Callers should catch and re-raise
        # with a meaningful upstream name if needed, but for the common case
        # the URL host provides enough context.
        raise ProxyUnavailableError(_upstream_from_url(url)) from exc
    except httpx.TimeoutException as exc:
        raise ProxyUnavailableError(_upstream_from_url(url)) from exc


async def async_proxy_stream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    timeout: float,
) -> AsyncIterator[bytes]:
    """Streaming counterpart to async_proxy — relays the upstream response
    body chunk-by-chunk instead of buffering it in full first.

    Yields raw bytes exactly as received from the upstream (e.g. api-gateway's
    SSE ``data: ...\\n\\n`` frames) — callers wrap this in a
    ``StreamingResponse`` and don't need to know the wire format.

    Raises:
        ProxyUnavailableError: on httpx.ConnectError or httpx.TimeoutException,
            including when either occurs mid-stream after some bytes have
            already been yielded (the caller has likely already started
            sending a response by then; there is no way to retract it, only
            to stop yielding further bytes).
    """
    try:
        async with client.stream(
            method.upper(), url, headers=headers, json=json, timeout=timeout
        ) as response:
            async for chunk in response.aiter_bytes():
                yield chunk
    except httpx.ConnectError as exc:
        raise ProxyUnavailableError(_upstream_from_url(url)) from exc
    except httpx.TimeoutException as exc:
        raise ProxyUnavailableError(_upstream_from_url(url)) from exc


# ---------------------------------------------------------------------------
# Streaming relay with in-band error conversion
# ---------------------------------------------------------------------------

async def sse_relay_with_inband_error(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    timeout: float,
) -> AsyncIterator[bytes]:
    """Relay an upstream SSE response chunk-by-chunk, converting an
    unreachable upstream into an in-band SSE error frame + [DONE] instead of
    raising.

    Shared by /portal/chat/completions and /portal/playground/chat's
    streaming paths — api_gateway's own streaming endpoint already always
    returns HTTP 200 and signals failures in-band (see
    api_gateway/routers/chat.py's sse_relay()); the only failure mode this
    proxy itself needs to handle is api_gateway being unreachable, which can
    happen before or after some bytes have already been relayed. Either way,
    this always ends the stream with a valid SSE error frame + [DONE] rather
    than letting the exception escape (the response has already started by
    the time a mid-stream failure could happen, so its HTTP status can't
    change).
    """
    try:
        async for chunk in async_proxy_stream(client, method, url, headers=headers, json=json, timeout=timeout):
            yield chunk
    except ProxyUnavailableError:
        yield (
            "data: "
            + _json_module.dumps({"error": {"code": "502", "message": "The API Gateway is unreachable or timed out."}})
            + "\n\n"
        ).encode()
        yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _upstream_from_url(url: str) -> str:
    """Extract a short host label from a URL for use in error messages."""
    try:
        parsed = httpx.URL(url)
        return parsed.host or url
    except Exception:
        return url
