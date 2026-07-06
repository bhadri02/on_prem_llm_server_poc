"""
admin_portal/routers/audit.py

Audit proxy router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /audit/events
    Returns a time-windowed list of audit events from the Audit Store.
    Optional query parameters:
        from  — ISO-8601 datetime string (inclusive lower bound)
        to    — ISO-8601 datetime string (inclusive upper bound)
        limit — number of events to return (default 50, range 1–200)

    Validates that ``limit`` is in [1, 200] (HTTP 400 on failure).
    Validates that ``from`` and ``to`` are valid ISO-8601 strings and that
    ``from`` ≤ ``to`` (HTTP 400 on failure).
    Proxies to ``{AUDIT_STORE_URL}/events`` and returns results sorted
    descending by ``timestamp_utc``.

GET /audit/requests/{request_id}
    Returns all audit events for a single request_id.
    Validates that ``request_id`` matches the UUID v4 format (HTTP 400 on
    failure).  Returns an empty ``AuditEventList`` if the upstream has no
    records for the given ID.

    On upstream network failure or timeout, returns HTTP 502 with an
    ``ErrorResponse(error="upstream_unavailable", upstream="audit-store")``.

Metrics
-------
- ``llm_portal_requests_total``   incremented on every call (success or error).
- ``llm_portal_latency_seconds``  records proxy round-trip latency.
- ``llm_portal_errors_total``     incremented with the relevant ``error_code``
  on 400 (validation_error) and 502 (upstream_unavailable) responses.

Validates: Requirements 4.3, 4.4, 5.3, 5.4, 5.5, 5.6, 5.7
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from admin_portal.config import settings
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.audit import AuditEvent, AuditEventList
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.services.proxy import ProxyUnavailableError, async_proxy

# ---------------------------------------------------------------------------
# Module-level HTTP client — reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_client = httpx.AsyncClient()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT_EVENTS = "/portal/audit/events"
_ENDPOINT_REQUESTS = "/portal/audit/requests/{request_id}"
_PROXY_TIMEOUT = 10.0  # seconds (per design document)

# UUID v4 regex (case-insensitive)
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(tags=["audit"])


# ---------------------------------------------------------------------------
# GET /audit/events
# ---------------------------------------------------------------------------

@router.get(
    "/audit/events",
    summary="List audit events",
    description=(
        "Return a list of audit events from the Audit Store, optionally "
        "filtered by time range and capped by ``limit``.  Results are sorted "
        "descending by ``timestamp_utc``.  Returns HTTP 400 if ``limit`` is "
        "outside [1, 200] or if ``from``/``to`` are not valid ISO-8601 strings "
        "or if ``from`` > ``to``."
    ),
)
async def list_audit_events(
    from_dt: Optional[str] = Query(None, alias="from", description="ISO-8601 start datetime (inclusive)"),
    to_dt: Optional[str] = Query(None, alias="to", description="ISO-8601 end datetime (inclusive)"),
    limit: int = Query(50, description="Maximum number of events to return (1–200)"),
) -> Response:
    """Proxy a request for audit events to the Audit Store.

    - Validates ``limit`` ∈ [1, 200]; returns HTTP 400 on failure.
    - Validates ``from`` and ``to`` as ISO-8601 datetimes, and that
      ``from`` ≤ ``to``; returns HTTP 400 on failure.
    - Forwards to ``{AUDIT_STORE_URL}/events`` with the query params.
    - Sorts the returned events descending by ``timestamp_utc``.
    - Returns HTTP 502 on upstream network failure or timeout.
    """
    t_start = time.monotonic()

    # --- Req 5.3: Validate limit ∈ [1, 200] --------------------------------
    if not (1 <= limit <= 200):
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_EVENTS, status="4xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_EVENTS, error_code="validation_error"
        ).inc()
        error_body = ErrorResponse(
            error="validation_error",
            message=f"'limit' must be between 1 and 200; got {limit}.",
            allowed_values=["1", "200"],
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=400,
            media_type="application/json",
        )

    # --- Req 5.6: Validate from/to as ISO-8601 and from ≤ to ---------------
    parsed_from: Optional[datetime] = None
    parsed_to: Optional[datetime] = None

    if from_dt is not None:
        try:
            parsed_from = datetime.fromisoformat(from_dt)
        except ValueError:
            latency = time.monotonic() - t_start
            llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
            llm_portal_requests_total.labels(endpoint=_ENDPOINT_EVENTS, status="4xx").inc()
            llm_portal_errors_total.labels(
                endpoint=_ENDPOINT_EVENTS, error_code="validation_error"
            ).inc()
            error_body = ErrorResponse(
                error="validation_error",
                message=f"'from' is not a valid ISO-8601 datetime: {from_dt!r}.",
            )
            return Response(
                content=error_body.model_dump_json(),
                status_code=400,
                media_type="application/json",
            )

    if to_dt is not None:
        try:
            parsed_to = datetime.fromisoformat(to_dt)
        except ValueError:
            latency = time.monotonic() - t_start
            llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
            llm_portal_requests_total.labels(endpoint=_ENDPOINT_EVENTS, status="4xx").inc()
            llm_portal_errors_total.labels(
                endpoint=_ENDPOINT_EVENTS, error_code="validation_error"
            ).inc()
            error_body = ErrorResponse(
                error="validation_error",
                message=f"'to' is not a valid ISO-8601 datetime: {to_dt!r}.",
            )
            return Response(
                content=error_body.model_dump_json(),
                status_code=400,
                media_type="application/json",
            )

    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_EVENTS, status="4xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_EVENTS, error_code="validation_error"
        ).inc()
        error_body = ErrorResponse(
            error="validation_error",
            message="'from' must not be later than 'to'.",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=400,
            media_type="application/json",
        )

    # --- Build upstream URL with query params --------------------------------
    params: dict[str, str] = {"limit": str(limit)}
    if from_dt is not None:
        params["from"] = from_dt
    if to_dt is not None:
        params["to"] = to_dt

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    upstream_url = f"{settings.AUDIT_STORE_URL}/audit/events?{query_string}"

    # --- Req 4.3, 4.4: Proxy and sort descending by timestamp_utc -----------
    try:
        upstream_response = await async_proxy(
            _client,
            "GET",
            upstream_url,
            timeout=_PROXY_TIMEOUT,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_EVENTS, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_EVENTS, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_EVENTS).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT_EVENTS,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    # Parse, sort descending by timestamp_utc, and return
    raw = upstream_response.json()
    # Audit store returns a plain list, not a dict with "events" key
    events = [AuditEvent(**e) for e in raw] if isinstance(raw, list) else []
    events.sort(key=lambda e: e.timestamp_utc, reverse=True)
    result = AuditEventList(events=events)

    return Response(
        content=result.model_dump_json(),
        status_code=200,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /audit/requests/{request_id}
# ---------------------------------------------------------------------------

@router.get(
    "/audit/requests/{request_id}",
    summary="Get audit events for a request",
    description=(
        "Return all audit events associated with a specific ``request_id``.  "
        "The ``request_id`` must be a valid UUID v4 string (HTTP 400 on "
        "failure).  Returns an empty list if the Audit Store has no records "
        "for the given ID.  Returns HTTP 502 on upstream network failure."
    ),
)
async def get_audit_by_request(request_id: str) -> Response:
    """Proxy a request for a single request's audit trail to the Audit Store.

    - Validates ``request_id`` against UUID v4 format; returns HTTP 400 on failure.
    - Forwards to ``{AUDIT_STORE_URL}/requests/{request_id}``.
    - Returns an empty ``AuditEventList`` if the upstream has no records.
    - Returns HTTP 502 on upstream network failure or timeout.
    """
    endpoint = _ENDPOINT_REQUESTS.format(request_id=request_id)
    t_start = time.monotonic()

    # --- Req 5.4: Validate request_id as UUID v4 ----------------------------
    if not _UUID_V4_RE.match(request_id):
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
        llm_portal_requests_total.labels(endpoint=endpoint, status="4xx").inc()
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="validation_error"
        ).inc()
        error_body = ErrorResponse(
            error="validation_error",
            message=f"'request_id' is not a valid UUID v4: {request_id!r}.",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=400,
            media_type="application/json",
        )

    upstream_url = f"{settings.AUDIT_STORE_URL}/audit/requests/{request_id}"

    # --- Req 5.5, 5.7: Proxy and handle empty / unavailable upstream --------
    try:
        upstream_response = await async_proxy(
            _client,
            "GET",
            upstream_url,
            timeout=_PROXY_TIMEOUT,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
        llm_portal_requests_total.labels(endpoint=endpoint, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=endpoint,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    # Req 5.5: return empty list if upstream has no records (404 or empty body)
    if upstream_response.status_code == 404:
        result = AuditEventList(events=[])
    else:
        raw = upstream_response.json()
        # Audit store returns a plain list, not a dict with "events" key
        events_data = raw if isinstance(raw, list) else []
        if not events_data:
            result = AuditEventList(events=[])
        else:
            result = AuditEventList(events=[AuditEvent(**e) for e in events_data])

    return Response(
        content=result.model_dump_json(),
        status_code=200,
        media_type="application/json",
    )
