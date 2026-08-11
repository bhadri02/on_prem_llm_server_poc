"""
admin_portal/routers/governance.py

Governance / security / usage summary router for the Admin/Developer Portal
(Layer 10).

Endpoints
---------
GET /governance/summary
    Proxies GET {AUDIT_STORE_URL}/audit/governance/summary, forwarding the
    optional ``from``/``to`` ISO-8601 UTC range query params unchanged.

    This is the durable, historical-counts complement to
    GET /portal/metrics/summary: that endpoint depends on a live Prometheus
    server for live per-second *rates* and returns null fields when
    Prometheus is unreachable (the common case in local dev, since nothing
    here runs a local Prometheus by default); this endpoint reads directly
    from the Audit Store's own SQLite audit trail, which is always
    populated and reachable whenever the platform is actually processing
    requests. Use this one for "how many requests were blocked and why",
    "how many tokens have we used", "which models are actually serving
    traffic" — use metrics/summary for live rate/error/cache-hit percentages.

    On upstream network failure, timeout, or a malformed (non-JSON) body,
    returns HTTP 502 with ``ErrorResponse(upstream="audit-store")``.
    A validation error from the Audit Store on a malformed ``from``/``to``
    (HTTP 422) is relayed through unchanged.

Metrics
-------
- ``llm_portal_requests_total``   incremented on every call (success or error).
- ``llm_portal_latency_seconds``  records proxy round-trip latency.
- ``llm_portal_errors_total``     incremented with ``error_code="upstream_unavailable"``
  on 502.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from admin_portal.config import settings
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.schemas.governance import GovernanceSummary
from admin_portal.services.proxy import ProxyUnavailableError, async_proxy
from admin_portal.services.session_auth import require_admin

# ---------------------------------------------------------------------------
# Module-level HTTP client — reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_client = httpx.AsyncClient()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT = "/portal/governance/summary"
_PROXY_TIMEOUT = 10.0  # seconds — matches routers/audit.py's proxy timeout

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(tags=["governance"], dependencies=[Depends(require_admin)])


@router.get(
    "/governance/summary",
    summary="Get AI governance / security / usage summary",
    description=(
        "Aggregate blocked-request counts (and reasons), prompt-injection "
        "flags, PII detection counts, token usage, and per-model request "
        "counts directly from the Audit Store's real audit trail. Returns "
        "HTTP 502 if the Audit Store is unreachable."
    ),
    response_model=GovernanceSummary,
)
async def get_governance_summary(
    from_dt: Optional[str] = Query(None, alias="from", description="ISO-8601 UTC start datetime (inclusive)"),
    to_dt: Optional[str] = Query(None, alias="to", description="ISO-8601 UTC end datetime (inclusive)"),
) -> Response:
    """Proxy a governance/security/usage summary request to the Audit Store."""
    t_start = time.monotonic()

    params: dict[str, str] = {}
    if from_dt is not None:
        params["from"] = from_dt
    if to_dt is not None:
        params["to"] = to_dt

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    upstream_url = f"{settings.AUDIT_STORE_URL}/audit/governance/summary"
    if query_string:
        upstream_url = f"{upstream_url}?{query_string}"

    try:
        upstream_response = await async_proxy(
            _client,
            "GET",
            upstream_url,
            timeout=_PROXY_TIMEOUT,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT, error_code="upstream_unavailable"
        ).inc()
        error_body = ErrorResponse(
            error="upstream_unavailable",
            message="The Audit Store is unreachable or timed out.",
            upstream="audit-store",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=502,
            media_type="application/json",
        )

    latency = time.monotonic() - t_start
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    # Relay a non-2xx upstream response (e.g. 422 on a malformed from/to)
    # through unchanged rather than reshaping it.
    if upstream_response.status_code < 200 or upstream_response.status_code >= 300:
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type="application/json",
        )

    try:
        raw = upstream_response.json()
    except ValueError:
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT, error_code="upstream_unavailable"
        ).inc()
        error_body = ErrorResponse(
            error="upstream_unavailable",
            message="The Audit Store returned a malformed response.",
            upstream="audit-store",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=502,
            media_type="application/json",
        )

    summary = GovernanceSummary(**raw)
    return Response(
        content=summary.model_dump_json(),
        status_code=200,
        media_type="application/json",
    )
