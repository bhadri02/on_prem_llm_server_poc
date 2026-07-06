"""
Downstream HTTP client for the API Gateway (Layer 1).

Forwards IMFDocuments to the Security & Governance layer over plain HTTP
(POC: no mTLS / gRPC required).

Validates: Requirements 5.1–5.5
"""

from __future__ import annotations

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
