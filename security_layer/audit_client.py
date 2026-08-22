"""
audit_client.py — Fire-and-forget audit event writer for the Security & Governance Layer.

Posts audit events to the Audit Store over plain HTTP (POC phase).
All failures are logged as WARNING and never re-raised so that audit
write errors cannot block the main request pipeline.

Reliability: a bare fire-and-forget POST silently drops the event on any
audit_store blip — since audit_store is also the source of truth for the
governance/compliance summary, that's a real (if usually brief) data-loss
window, not just a log-noise annoyance. post_audit_event now retries a
couple of times with a short backoff before giving up, and if it still
fails, hands the event to a bounded in-process queue (_pending) that
flush_pending_audit_events() (called on an interval from main.py's
lifespan) keeps retrying. This is deliberately in-memory, not
disk-spooled — it survives a brief audit_store outage while this process
keeps running, but not a restart of this service itself; that's an accepted
tradeoff to avoid adding a new persistent volume just for this. If the
queue itself fills up (audit_store down for a long time), the oldest
pending events are dropped to bound memory use, logged once per drop.
"""

import asyncio
from collections import deque

import httpx

from security_layer.logging_config import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0.3, 0.8)  # delay before attempt 2, then attempt 3
MAX_PENDING = 500

# Bounded in-process queue of events that exhausted their retries — drained
# by flush_pending_audit_events() on an interval (see main.py's lifespan).
_pending: deque[tuple[dict, str, str]] = deque(maxlen=MAX_PENDING)
_dropped_total = 0


async def _post_once(event: dict, url: str, api_key: str) -> bool:
    """Single POST attempt. Returns True on a 2xx response, False otherwise."""
    request_id = event.get("request_id")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                f"{url}/audit/events",
                json=event,
                headers={"X-API-Key": api_key},
            )
        if resp.status_code < 300:
            return True
        logger.warning(
            "audit_write_non_2xx",
            extra={"extra_fields": {"request_id": request_id, "status_code": resp.status_code}},
        )
        return False
    except httpx.TimeoutException:
        logger.warning(
            "audit_write_timeout",
            extra={"extra_fields": {"request_id": request_id, "timeout": True}},
        )
        return False
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            extra={"extra_fields": {"request_id": request_id, "error": str(exc)}},
        )
        return False


async def post_audit_event(event: dict, url: str, api_key: str) -> None:
    """Non-blocking audit write with short retries; never raises.

    Retries up to MAX_ATTEMPTS times with a short backoff. If every attempt
    fails, the event is queued in _pending for flush_pending_audit_events()
    to keep retrying rather than being dropped outright.

    Args:
        event:   Audit record dict; must contain a ``request_id`` key.
        url:     Base URL of the Audit Store (e.g. ``http://audit-store:9200``).
        api_key: API key sent in the ``X-API-Key`` request header.
    """
    for attempt in range(MAX_ATTEMPTS):
        if await _post_once(event, url, api_key):
            return
        if attempt < len(RETRY_BACKOFF_SECONDS):
            await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

    _queue_pending(event, url, api_key)


def _queue_pending(event: dict, url: str, api_key: str) -> None:
    global _dropped_total
    if len(_pending) == _pending.maxlen:
        _dropped_total += 1
        logger.warning(
            "audit_pending_queue_full_dropping_oldest",
            extra={"extra_fields": {"dropped_total": _dropped_total}},
        )
    _pending.append((event, url, api_key))


async def flush_pending_audit_events() -> None:
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
            event, url, api_key = _pending.popleft()
        except IndexError:
            break
        if await _post_once(event, url, api_key):
            flushed += 1
        else:
            _pending.append((event, url, api_key))

    if flushed:
        logger.info(
            "audit_pending_queue_flushed",
            extra={"extra_fields": {"flushed": flushed, "still_pending": len(_pending)}},
        )
