"""
audit_client.py — Fire-and-forget audit event writer for the Security & Governance Layer.

Posts audit events to the Audit Store over plain HTTP (POC phase).
All failures are logged as WARNING and never re-raised so that audit
write errors cannot block the main request pipeline.
"""

import httpx

from security_layer.logging_config import get_logger

logger = get_logger(__name__)


async def post_audit_event(event: dict, url: str, api_key: str) -> None:
    """Non-blocking audit write. Failures are logged as WARNING, never raised.

    Args:
        event:   Audit record dict; must contain a ``request_id`` key.
        url:     Base URL of the Audit Store (e.g. ``http://audit-store:9200``).
        api_key: API key sent in the ``X-API-Key`` request header.
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                f"{url}/audit/events",
                json=event,
                headers={"X-API-Key": api_key},
            )
        if resp.status_code >= 300:
            logger.warning(
                "audit_write_non_2xx",
                extra={
                    "extra_fields": {
                        "request_id": event.get("request_id"),
                        "status_code": resp.status_code,
                    }
                },
            )
    except httpx.TimeoutException:
        logger.warning(
            "audit_write_timeout",
            extra={
                "extra_fields": {
                    "request_id": event.get("request_id"),
                    "timeout": True,
                }
            },
        )
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            extra={
                "extra_fields": {
                    "request_id": event.get("request_id"),
                    "error": str(exc),
                }
            },
        )
