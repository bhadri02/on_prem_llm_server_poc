"""
intelligent_router/audit_client.py

Fire-and-forget audit event writer for the Intelligent Router (Layer 3).

The sole public function, post_audit_event, POSTs an audit event dict to the
Audit Store.  It is always dispatched via FastAPI BackgroundTask so the HTTP
response to the caller is sent before the POST completes.

Failure behaviour — every exception branch logs a WARNING and returns; the
function never raises under any circumstance:
  - httpx.TimeoutException  → WARNING with "timeout" in the message
  - non-2xx response        → WARNING with request_id and status code
  - any other exception     → WARNING with request_id and error string
"""

import httpx

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)

AUDIT_TIMEOUT = 2.0


async def post_audit_event(
    event: dict,
    audit_store_url: str,
    http_client: httpx.AsyncClient,
    api_key: str = "",
) -> None:
    """Non-blocking audit write. Failures are logged as WARNING, never raised.

    Args:
        event:           The audit event payload to POST as JSON.
        audit_store_url: Base URL of the Audit Store (e.g. "http://audit-store:9200").
        http_client:     Shared httpx.AsyncClient; caller manages its lifecycle.
        api_key:         X-API-Key header value for the Audit Store.
    """
    request_id = event.get("request_id")
    try:
        resp = await http_client.post(
            f"{audit_store_url}/audit/events",
            json=event,
            headers={"X-API-Key": api_key},
            timeout=AUDIT_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning(
                "audit_write_non_2xx",
                extra={
                    "extra_fields": {
                        "request_id": request_id,
                        "status_code": resp.status_code,
                    }
                },
            )
    except httpx.TimeoutException:
        logger.warning(
            "audit_write_timeout",
            extra={"extra_fields": {"request_id": request_id}},
        )
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            extra={"extra_fields": {"request_id": request_id, "error": str(exc)}},
        )
