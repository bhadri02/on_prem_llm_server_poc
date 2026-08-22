"""
Downstream HTTP client for the API Gateway (Layer 1).

Forwards IMFDocuments to the Security & Governance layer over plain HTTP
(POC: no mTLS / gRPC required).

Validates: Requirements 5.1–5.5
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from api_gateway.config import get_settings
from api_gateway.schemas.imf import IMFDocument


class DownstreamError(Exception):
    """Raised when a downstream service call cannot be completed successfully."""

    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self.body = body or {}
        super().__init__(f"Downstream service error: {status_code}")


async def forward_to_security(
    imf: IMFDocument,
    client: httpx.AsyncClient,
) -> IMFDocument:
    """POST an IMFDocument to the Security & Governance layer for processing.

    Returns the updated IMFDocument on HTTP 200.
    Raises DownstreamError carrying the original status_code and body for
    any non-200 response so the caller can relay security blocks (400/403)
    back to the client instead of always returning 502.
    """
    settings = get_settings()
    url = f"{settings.downstream_security_url}/security/check"

    try:
        response = await client.post(
            url,
            json=imf.model_dump(),
            headers={"Content-Type": "application/json"},
            timeout=settings.downstream_timeout_seconds,
        )
    except httpx.TimeoutException:
        raise DownstreamError(502)
    except httpx.ConnectError:
        raise DownstreamError(502)
    except httpx.RequestError:
        raise DownstreamError(502)

    if response.status_code != 200:
        # Relay the exact status + body so security blocks surface correctly
        try:
            body = response.json()
        except Exception:
            body = {}
        raise DownstreamError(response.status_code, body)

    raw = response.content
    if not raw:
        raise DownstreamError(502)

    try:
        payload = response.json()
    except Exception:
        raise DownstreamError(502)

    return IMFDocument.model_validate(payload)


async def forward_to_security_stream(
    imf: IMFDocument,
    client: httpx.AsyncClient,
) -> AsyncIterator[dict]:
    """Streaming counterpart to forward_to_security.

    POSTs to ``{security_url}/security/check/stream`` and yields each
    parsed newline-delimited-JSON line unchanged (see security_layer's
    streaming wire protocol — routers/pre_check.py's module docstring):

        {"type": "delta", "content": "<text>"}
        {"type": "done", "imf": {...}}
        {"type": "error", "event": "<code>", "status_code": <int>, ...}

    The caller (routers/chat.py) is responsible for translating this into
    OpenAI-compatible SSE frames — this function only relays the security
    layer's stream unchanged.

    Raises:
        DownstreamError(502): on any connection failure, timeout, non-200
            response, or a stream line that isn't valid JSON.
    """
    settings = get_settings()
    url = f"{settings.downstream_security_url}/security/check/stream"

    try:
        async with client.stream(
            "POST",
            url,
            json=imf.model_dump(),
            headers={"Content-Type": "application/json"},
            timeout=settings.downstream_timeout_seconds,
        ) as response:
            if response.status_code != 200:
                raise DownstreamError(502)

            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except DownstreamError:
                    raise
                except Exception:
                    raise DownstreamError(502)

    except DownstreamError:
        raise
    except httpx.TimeoutException:
        raise DownstreamError(502)
    except httpx.ConnectError:
        raise DownstreamError(502)
    except httpx.RequestError:
        raise DownstreamError(502)
