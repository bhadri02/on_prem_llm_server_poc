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
    full_imf: dict | None = None,
) -> dict:
    """POST /cache/lookup.

    Sends the full IMF envelope if provided, otherwise builds a minimal one.
    Returns the raw response dict on HTTP 200.
    On any failure returns ``{"hit": False}`` and logs a WARNING. Never raises.
    """
    if full_imf is not None:
        payload = full_imf
    else:
        payload = {
            "request_id": request_id,
            "request": {"messages": messages, "task_type": task_type, "model": model},
            "routing": {"selected_model": model},
        }
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/lookup",
            json=payload,
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

    Sends the full IMF envelope (response_imf) as the body.
    Failures are logged as WARNING and never re-raised.
    """
    request_id = response_imf.get("request_id")
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/write",
            json=response_imf,
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
