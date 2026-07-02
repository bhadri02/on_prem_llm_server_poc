"""
intelligent_router/cache_client.py

HTTP client for the Cache Layer (port 8086).

Provides two coroutines:

  cache_lookup  — POST /cache/lookup
                  Returns the parsed response dict on HTTP 200.
                  Returns {"hit": False} on any failure (non-200, timeout,
                  connection error, or any other exception). Never raises.

  cache_write   — POST /cache/write (fire-and-forget)
                  Logs a WARNING on non-200, timeout, or connection failure.
                  Never raises. Always called via FastAPI BackgroundTask so
                  the caller's response has already been returned before this
                  coroutine executes.
"""

import httpx

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)

CACHE_TIMEOUT = 3.0


async def cache_lookup(
    messages: list[dict],
    model: str,
    task_type: str,
    request_id: str,
    cache_url: str,
    http_client: httpx.AsyncClient,
) -> dict:
    """POST /cache/lookup.

    Returns the raw response dict on HTTP 200.
    On any failure (non-200, timeout, connection error, parse error) returns
    ``{"hit": False}`` and logs a WARNING with ``request_id`` and the failure
    reason. Never raises.
    """
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/lookup",
            json={
                "messages": messages,
                "model": model,
                "task_type": task_type,
                "request_id": request_id,
            },
            timeout=CACHE_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(
            "cache_lookup_non_200",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "status_code": resp.status_code,
                }
            },
        )
    except httpx.TimeoutException:
        logger.warning(
            "cache_lookup_timeout",
            extra={"extra_fields": {"request_id": request_id}},
        )
    except Exception as exc:
        logger.warning(
            "cache_lookup_failed",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "error": str(exc),
                }
            },
        )
    return {"hit": False}


async def cache_write(
    messages: list[dict],
    model: str,
    task_type: str,
    response_imf: dict,
    cache_url: str,
    http_client: httpx.AsyncClient,
) -> None:
    """Fire-and-forget POST /cache/write.

    Failures (non-200, timeout, connection error, any exception) are logged
    as WARNING and never re-raised. Always called via FastAPI BackgroundTask.
    """
    request_id = response_imf.get("request_id")
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/write",
            json={
                "messages": messages,
                "model": model,
                "task_type": task_type,
                "response_imf": response_imf,
            },
            timeout=CACHE_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning(
                "cache_write_non_200",
                extra={
                    "extra_fields": {
                        "request_id": request_id,
                        "status_code": resp.status_code,
                    }
                },
            )
    except httpx.TimeoutException:
        logger.warning(
            "cache_write_timeout",
            extra={"extra_fields": {"request_id": request_id}},
        )
    except Exception as exc:
        logger.warning(
            "cache_write_failed",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "error": str(exc),
                }
            },
        )
