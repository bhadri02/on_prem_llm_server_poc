"""
intelligent_router/health_checker.py

HTTP health checker for model backends.

Provides:
  check_model_health(health_url, http_client, timeout_seconds) -> bool
    Issues an HTTP GET to the given health_url and returns True only when the
    response status is exactly 200.  All other outcomes — non-200 statuses
    (including 3xx redirects), timeouts, and connection errors — return False.

Design note: follow_redirects=False means httpx returns the 3xx response
object rather than following it, so the status_code == 200 check naturally
rejects redirects as failures (Requirement 4.6).
"""

import httpx

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)


async def check_model_health(
    health_url: str,
    http_client: httpx.AsyncClient,
    timeout_seconds: float,
) -> bool:
    """Issue GET to *health_url* and return True only for HTTP 200.

    Args:
        health_url: The URL to GET (e.g. ``http://inference-ollama:11434/api/tags``).
        http_client: Shared ``httpx.AsyncClient`` — callers must not close it.
        timeout_seconds: Per-request timeout in seconds (Requirement 4.1 specifies 5 s).

    Returns:
        ``True`` when the response status code is exactly 200.
        ``False`` for any non-200 status, timeout, or connection failure.
    """
    try:
        resp = await http_client.get(
            health_url,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        return resp.status_code == 200
    except (httpx.TimeoutException, httpx.ConnectError):
        return False
