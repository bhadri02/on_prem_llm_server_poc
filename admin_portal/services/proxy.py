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

from typing import Any, Dict, Optional

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
