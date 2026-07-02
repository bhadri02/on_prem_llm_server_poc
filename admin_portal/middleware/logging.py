"""
admin_portal/middleware/logging.py

Structured JSON logging middleware for the Admin/Developer Portal (Layer 10).

Emits exactly one single-line JSON object to stdout for every HTTP request,
including requests that raise an unhandled exception.

Log entry fields
----------------
  endpoint    (str)   — request URL path, e.g. "/portal/models"
  status_code (int)   — HTTP response status code (500 for unhandled exceptions)
  latency_ms  (float) — elapsed time in milliseconds, rounded to 2 decimal
                        places, always non-negative

Usage
-----
    from admin_portal.middleware.logging import LoggingMiddleware
    app.add_middleware(LoggingMiddleware)

Implementation notes
--------------------
* Does NOT import from ``admin_portal.metrics`` — metric increments are the
  responsibility of a separate prometheus middleware or the route handlers.
* Uses ``time.perf_counter()`` for sub-millisecond accuracy.
* The ``flush=True`` argument ensures each line is flushed immediately, which
  is important in containerised environments where stdout may be line-buffered.

Validates: Requirements 3.6, 10.1
"""

from __future__ import annotations

import json
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class LoggingMiddleware(BaseHTTPMiddleware):
    """Emit one structured JSON log line to stdout per HTTP request.

    The log line is written after the response is produced (or after an
    unhandled exception is caught), so ``latency_ms`` covers the full
    round-trip through all downstream middleware and route handlers.

    Exception handling
    ------------------
    If ``call_next`` raises, the exception is caught, a log entry is emitted
    with ``status_code=500``, and the exception is re-raised so that
    FastAPI's default exception handler can return a proper error response to
    the client.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            entry = {
                "endpoint": request.url.path,
                "status_code": 500,
                "latency_ms": latency_ms,
            }
            print(json.dumps(entry), flush=True)
            raise  # re-raise so upstream handlers can respond

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        entry = {
            "endpoint": request.url.path,
            "status_code": status_code,
            "latency_ms": latency_ms,
        }
        print(json.dumps(entry), flush=True)

        return response
