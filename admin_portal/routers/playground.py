"""
admin_portal/routers/playground.py

Playground router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
POST /playground/chat
    Accepts a ``ChatRequest`` body (validated by Pydantic — invalid bodies
    return HTTP 422 automatically).  Forwards the request unchanged to the
    API Gateway's ``/v1/chat/completions`` endpoint and propagates the upstream
    response (status code + body) back to the caller.

    On upstream network failure or timeout, returns HTTP 502 with an
    ``ErrorResponse(error="upstream_unavailable", upstream="api-gateway")``.

Metrics
-------
- ``llm_portal_requests_total``   incremented on every call (success or 502).
- ``llm_portal_latency_seconds``  records proxy round-trip latency.
- ``llm_portal_errors_total``     incremented with ``error_code="upstream_unavailable"`` on 502.

JSON request/response logging is handled by the middleware — nothing extra here.

Validates: Requirements 3.3, 3.4
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter
from fastapi.responses import Response

from admin_portal.config import settings
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.schemas.playground import ChatRequest
from admin_portal.services.proxy import ProxyUnavailableError, async_proxy

# ---------------------------------------------------------------------------
# Module-level HTTP client — reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_client = httpx.AsyncClient()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT = "/portal/playground/chat"
_PROXY_TIMEOUT = 30.0  # seconds

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(tags=["playground"])


@router.post(
    "/playground/chat",
    summary="Playground chat",
    description=(
        "Forward a chat request to the API Gateway and return its response "
        "unchanged.  Returns HTTP 422 on invalid request body, HTTP 502 if "
        "the API Gateway is unreachable."
    ),
)
async def playground_chat(body: ChatRequest) -> Response:
    """Proxy a chat completion request to the API Gateway.

    - Validates the ``ChatRequest`` body via Pydantic (FastAPI returns 422 on
      validation failure before this handler is reached).
    - Forwards the request to ``{API_GATEWAY_URL}/v1/chat/completions`` with
      the ``X-API-Key`` header and a 30-second timeout.
    - Propagates the upstream status code and body back to the caller unchanged.
    - Returns HTTP 502 on upstream network failure / timeout.
    """
    upstream_url = f"{settings.API_GATEWAY_URL}/v1/chat/completions"
    headers = {"X-API-Key": settings.GATEWAY_API_KEY}

    t_start = time.monotonic()
    try:
        upstream_response = await async_proxy(
            _client,
            "POST",
            upstream_url,
            headers=headers,
            json=body.model_dump(),
            timeout=_PROXY_TIMEOUT,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start

        # Record metrics for the failed request
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT, error_code="upstream_unavailable"
        ).inc()

        error_body = ErrorResponse(
            error="upstream_unavailable",
            message="The API Gateway is unreachable or timed out.",
            upstream="api-gateway",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=502,
            media_type="application/json",
        )

    latency = time.monotonic() - t_start

    # Record metrics for the successful (proxied) request
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )
