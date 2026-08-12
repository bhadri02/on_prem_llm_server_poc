"""
api_gateway/services/audit_client.py

Fire-and-forget audit event writer for the API Gateway (Layer 1), POSTing
to the Audit Store — same pattern as intelligent_router/audit_client.py and
security_layer/audit_client.py.

Previously api_gateway's own audit events (auth_fail, auth_pass,
rate_limited, request_received, response_sent) were written to stdout only
(api_gateway/services/audit.py::emit_audit_event) and never reached the
Audit Store, making 401/403/429 gateway-layer rejections invisible to
GET /portal/governance/summary and GET /portal/audit/events. This module
closes that gap — emit_audit_event() still writes to stdout for local
tail/debug visibility, and callers additionally schedule post_audit_event()
as a background task so the durable copy reaches the Audit Store too.

Failure behaviour — mirrors the other services' audit_client exactly: every
exception branch logs a WARNING and returns; the function never raises.
"""

from __future__ import annotations

import httpx
from fastapi import BackgroundTasks, Response

from api_gateway.config import get_settings
from api_gateway.schemas.audit import AuditEvent
from shared.observability.logging import emit, get_logger

AUDIT_TIMEOUT = 2.0


def _to_audit_store_payload(event: AuditEvent) -> dict:
    """Map api_gateway's AuditEvent onto the Audit Store's AuditEventCreate shape.

    api_gateway's schema carries a few HTTP-specific fields (method, path,
    status_code) that the Audit Store's schema has no column for — those are
    dropped. ``reason`` (e.g. auth_fail's "missing_header") has no dedicated
    column either, so it's folded into ``error_code`` when error_code itself
    isn't already set, mirroring how security_layer's own block events were
    fixed to populate error_code for exactly this kind of detail.
    """
    return {
        "audit_id": event.audit_id,
        "request_id": event.request_id,
        "timestamp_utc": event.timestamp_utc,
        "user_id": event.user_id,
        "department": event.department,
        "layer": "api_gateway",
        "event_type": event.event_type,
        "model_used": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": int(event.latency_ms) if event.latency_ms is not None else 0,
        "outcome": event.outcome,
        "error_code": event.error_code or event.reason,
        "pii_actions": [],
        "policy_decisions": [],
    }


async def post_audit_event(
    event: AuditEvent,
    audit_store_url: str,
    http_client: httpx.AsyncClient,
    api_key: str = "",
) -> None:
    """Non-blocking audit write. Failures are logged as WARNING, never raised.

    Args:
        event:           The api_gateway AuditEvent to POST (translated to
                         the Audit Store's schema first).
        audit_store_url: Base URL of the Audit Store (e.g. "http://audit-store:9200").
        http_client:     Shared httpx.AsyncClient; caller manages its lifecycle.
        api_key:         X-API-Key header value for the Audit Store.
    """
    try:
        resp = await http_client.post(
            f"{audit_store_url}/audit/events",
            json=_to_audit_store_payload(event),
            headers={"X-API-Key": api_key},
            timeout=AUDIT_TIMEOUT,
        )
        if resp.status_code >= 300:
            emit(
                get_logger(event.request_id),
                level="WARNING",
                event="audit_write_non_2xx",
                message="Audit Store rejected event",
                status_code=resp.status_code,
            )
    except httpx.TimeoutException:
        emit(
            get_logger(event.request_id),
            level="WARNING",
            event="audit_write_timeout",
            message="Audit Store write timed out",
        )
    except Exception as exc:
        emit(
            get_logger(event.request_id),
            level="WARNING",
            event="audit_write_failed",
            message="Audit Store write failed",
            error=str(exc),
        )


def schedule_audit_post(response: Response, event: AuditEvent, http_client: httpx.AsyncClient) -> None:
    """Attach a durable Audit Store write to *response* as a background task.

    For use from middleware (AuthMiddleware, RateLimitMiddleware), which —
    unlike a route handler — has no FastAPI-injected ``BackgroundTasks`` of
    its own; this uses ``response.background`` instead, Starlette's own
    mechanism for scheduling work that runs after the response is sent.

    A response returned by ``call_next()`` may already carry a
    ``BackgroundTasks`` instance set by the route handler (e.g. chat.py's
    own audit posts) — reuse it via ``.add_task()`` rather than overwriting
    ``.background`` wholesale, which would silently drop those.
    """
    if response.background is None:
        response.background = BackgroundTasks()
    settings = get_settings()
    response.background.add_task(
        post_audit_event,
        event,
        settings.audit_store_url,
        http_client,
        settings.audit_api_key,
    )
