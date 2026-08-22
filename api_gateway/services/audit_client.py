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

Reliability: a bare fire-and-forget POST silently drops the event on any
audit_store blip — since audit_store is also the source of truth for the
governance/compliance summary, that's a real (if usually brief) data-loss
window, not just a log-noise annoyance. post_audit_event now retries a
couple of times with a short backoff before giving up, and if it still
fails, hands the (already-translated) payload to a bounded in-process queue
(_pending) that flush_pending_audit_events() (called on an interval from
main.py's lifespan) keeps retrying. This is deliberately in-memory, not
disk-spooled — it survives a brief audit_store outage while this process
keeps running, but not a restart of this service itself; that's an accepted
tradeoff to avoid adding a new persistent volume just for this. If the
queue itself fills up (audit_store down for a long time), the oldest
pending events are dropped to bound memory use, logged once per drop.
"""

from __future__ import annotations

import asyncio
from collections import deque

import httpx
from fastapi import BackgroundTasks, Response

from api_gateway.config import get_settings
from api_gateway.schemas.audit import AuditEvent
from shared.observability.logging import emit, get_logger

AUDIT_TIMEOUT = 2.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0.3, 0.8)  # delay before attempt 2, then attempt 3
MAX_PENDING = 500

# Bounded in-process queue of (payload, url, api_key, request_id) tuples that
# exhausted their retries — drained by flush_pending_audit_events() on an
# interval (see main.py's lifespan). Stores the already-translated payload
# so a flush retry never needs the original AuditEvent object again.
_pending: deque[tuple[dict, str, str, str]] = deque(maxlen=MAX_PENDING)
_dropped_total = 0


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


async def _post_once(
    payload: dict, audit_store_url: str, http_client: httpx.AsyncClient, api_key: str, request_id: str
) -> bool:
    """Single POST attempt. Returns True on a 2xx response, False otherwise."""
    try:
        resp = await http_client.post(
            f"{audit_store_url}/audit/events",
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=AUDIT_TIMEOUT,
        )
        if resp.status_code < 300:
            return True
        emit(
            get_logger(request_id),
            level="WARNING",
            event="audit_write_non_2xx",
            message="Audit Store rejected event",
            status_code=resp.status_code,
        )
        return False
    except httpx.TimeoutException:
        emit(
            get_logger(request_id),
            level="WARNING",
            event="audit_write_timeout",
            message="Audit Store write timed out",
        )
        return False
    except Exception as exc:
        emit(
            get_logger(request_id),
            level="WARNING",
            event="audit_write_failed",
            message="Audit Store write failed",
            error=str(exc),
        )
        return False


async def post_audit_event(
    event: AuditEvent,
    audit_store_url: str,
    http_client: httpx.AsyncClient,
    api_key: str = "",
) -> None:
    """Non-blocking audit write with short retries; never raises.

    Retries up to MAX_ATTEMPTS times with a short backoff. If every attempt
    fails, the translated payload is queued in _pending for
    flush_pending_audit_events() to keep retrying rather than being dropped
    outright.

    Args:
        event:           The api_gateway AuditEvent to POST (translated to
                         the Audit Store's schema first).
        audit_store_url: Base URL of the Audit Store (e.g. "http://audit-store:9200").
        http_client:     Shared httpx.AsyncClient; caller manages its lifecycle.
        api_key:         X-API-Key header value for the Audit Store.
    """
    payload = _to_audit_store_payload(event)
    for attempt in range(MAX_ATTEMPTS):
        if await _post_once(payload, audit_store_url, http_client, api_key, event.request_id):
            return
        if attempt < len(RETRY_BACKOFF_SECONDS):
            await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

    _queue_pending(payload, audit_store_url, api_key, event.request_id)


def _queue_pending(payload: dict, url: str, api_key: str, request_id: str) -> None:
    global _dropped_total
    if len(_pending) == _pending.maxlen:
        _dropped_total += 1
        emit(
            get_logger(request_id),
            level="WARNING",
            event="audit_pending_queue_full_dropping_oldest",
            message="Audit pending queue full — dropping oldest event",
            dropped_total=_dropped_total,
        )
    _pending.append((payload, url, api_key, request_id))


async def flush_pending_audit_events(http_client: httpx.AsyncClient) -> None:
    """Retry every queued event once; re-queue whatever still fails.

    Called on an interval from main.py's lifespan. Never raises. Snapshots
    the current queue length up front so this makes bounded progress even
    if new events are being queued concurrently while it runs.
    """
    remaining = len(_pending)
    if not remaining:
        return

    flushed = 0
    for _ in range(remaining):
        try:
            payload, url, api_key, request_id = _pending.popleft()
        except IndexError:
            break
        if await _post_once(payload, url, http_client, api_key, request_id):
            flushed += 1
        else:
            _pending.append((payload, url, api_key, request_id))

    if flushed:
        emit(
            get_logger("audit-flush"),
            level="INFO",
            event="audit_pending_queue_flushed",
            message="Flushed queued audit events",
            flushed=flushed,
            still_pending=len(_pending),
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
