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
    """Raised when a downstream service call cannot be completed successfully.

    Args:
        status_code: The HTTP status code that best describes the failure
            (typically ``502`` for gateway errors).
    """

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Downstream service error: {status_code}")


async def forward_to_security(
    imf: IMFDocument,
    client: httpx.AsyncClient,
) -> IMFDocument:
    """POST an IMFDocument to the Security & Governance layer for processing.

    Sends the serialized IMF to ``{settings.downstream_security_url}/process``
    and returns the updated IMFDocument returned in the response body.

    Args:
        imf: The IMFDocument to forward.
        client: A shared :class:`httpx.AsyncClient` (injected by the caller
            so connections can be reused across requests).

    Returns:
        The updated :class:`IMFDocument` returned by the security layer.

    Raises:
        DownstreamError: With status code ``502`` for any of the following:
            - Network / connection errors (``httpx.ConnectError``,
              ``httpx.RequestError``)
            - Timeout (``httpx.TimeoutException``)
            - Non-200 HTTP response
            - Empty or non-JSON response body
    """
    settings = get_settings()
    url = f"{settings.downstream_security_url}/process"

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
        raise DownstreamError(502)

    # Guard against empty or non-JSON body
    raw = response.content
    if not raw:
        raise DownstreamError(502)

    try:
        payload = response.json()
    except Exception:
        raise DownstreamError(502)

    return IMFDocument.model_validate(payload)
