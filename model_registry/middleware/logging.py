"""
Structured JSON logging middleware for the Model Registry.

Implements LoggingMiddleware(BaseHTTPMiddleware) that emits one JSON log line
to stdout per HTTP request, containing: timestamp (ISO-8601 UTC), level
(INFO for 2xx/3xx/4xx, ERROR for 5xx), method, path, status_code, and
latency_ms. Respects the LOG_LEVEL environment variable to suppress
lower-priority entries. Never reads or logs the X-API-Key header value.
"""

import json
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from model_registry.config import get_settings

# Numeric log level priorities matching Python's logging module conventions.
_LEVEL_PRIORITY: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,    # alias
    "ERROR": 40,
    "CRITICAL": 50,
}


def _should_emit(level: str) -> bool:
    """Return True if *level* meets or exceeds the configured LOG_LEVEL.

    Rules (per Requirements 9.5):
    - If LOG_LEVEL is WARNING or higher, INFO entries are suppressed.
    - ERROR entries are always emitted regardless of configured level.
    - Unknown LOG_LEVEL values default to INFO priority (20).
    """
    # ERROR is always emitted — hard requirement 9.4 / 9.5
    if level == "ERROR":
        return True

    settings = get_settings()
    configured = settings.log_level.upper()
    configured_priority = _LEVEL_PRIORITY.get(configured, _LEVEL_PRIORITY["INFO"])
    entry_priority = _LEVEL_PRIORITY.get(level.upper(), _LEVEL_PRIORITY["INFO"])

    return entry_priority >= configured_priority


class LoggingMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that emits one structured JSON log line per request.

    The log entry is written to stdout via ``print(..., flush=True)`` after
    the response is returned so that the status code and latency are both
    available. The X-API-Key header value is never read or logged.

    Log entry fields (Requirements 9.1 – 9.6):
        timestamp   — ISO-8601 UTC datetime with "Z" suffix
        level       — "INFO" (status < 500) or "ERROR" (status >= 500)
        method      — HTTP method (GET, POST, …)
        path        — URL path component
        status_code — integer HTTP response status code
        latency_ms  — elapsed time in milliseconds, rounded to 2 decimal places
    """

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = (time.monotonic() - start) * 1000

        level = "ERROR" if response.status_code >= 500 else "INFO"

        if _should_emit(level):
            entry = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "level": level,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
            }
            print(json.dumps(entry), flush=True)  # stdout only — never logs X-API-Key

        return response
