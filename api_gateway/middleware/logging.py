"""
Structured JSON logging middleware for the API Gateway (Layer 1).

Emits one structured JSON line per request to stdout containing:
    request_id, timestamp (ISO-8601 UTC with "Z" suffix), method, path,
    status_code, latency_ms

request_id derivation:
    1. Read from request.state.request_id (set by AuthMiddleware on auth_pass)
    2. If not yet set, generate a fallback UUID v4 and store it on request.state

Respects LOG_LEVEL from get_settings() / the LOG_LEVEL env var; uses Python's
stdlib logging module to determine whether a record should be emitted.

Unhandled exceptions during call_next are caught, logged at ERROR level with
    request_id, exception_type, exception_message, traceback, latency_ms
and the handler returns a JSON 500 response rather than re-raising so that
the caller always receives a well-formed error body.

Output format examples
----------------------
Normal request:
    {"request_id": "...", "timestamp": "2024-01-01T00:00:00Z",
     "method": "POST", "path": "/v1/chat/completions",
     "status_code": 200, "latency_ms": 42.5}

Unhandled exception:
    {"level": "ERROR", "request_id": "...", "exception_type": "ValueError",
     "exception_message": "...", "traceback": "...", "latency_ms": 42.5}

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import json
import logging
import time
import traceback as tb_module
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.config import get_settings

# ---------------------------------------------------------------------------
# Level helpers
# ---------------------------------------------------------------------------

_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _configured_level() -> int:
    """Return the numeric log level derived from the LOG_LEVEL setting."""
    raw = get_settings().log_level.upper()
    return _LEVEL_MAP.get(raw, logging.INFO)


def _should_emit(numeric_level: int) -> bool:
    """Return True if *numeric_level* is at or above the configured threshold."""
    return numeric_level >= _configured_level()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class LoggingMiddleware(BaseHTTPMiddleware):
    """Emit one structured JSON log line to stdout per HTTP request.

    Normal log entry fields (Requirement 8.1):
        request_id  — from request.state or a generated fallback UUID v4
        timestamp   — ISO-8601 UTC datetime with "Z" suffix
        method      — HTTP method
        path        — URL path
        status_code — integer HTTP response status code
        latency_ms  — elapsed time in milliseconds (float, 2 dp)

    Error log entry fields (Requirement 8.5, for unhandled exceptions):
        level             — "ERROR"
        request_id        — as above
        exception_type    — type(exc).__name__
        exception_message — str(exc)
        traceback         — formatted traceback string
        latency_ms        — elapsed ms up to point of failure
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # --- Resolve / assign request_id -----------------------------------
        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.monotonic()

        # --- Call the next handler, catching unhandled exceptions ----------
        try:
            response = await call_next(request)
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 2)

            if _should_emit(logging.ERROR):
                error_entry = {
                    "level": "ERROR",
                    "request_id": request_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": tb_module.format_exc(),
                    "latency_ms": latency_ms,
                }
                try:
                    print(json.dumps(error_entry), flush=True)
                except Exception:
                    pass  # silently discard on stdout failure

            # Return a well-formed 500 rather than letting the exception bubble
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "500", "message": "Internal server error"}},
            )

        latency_ms = round((time.monotonic() - start) * 1000, 2)

        # --- Emit normal request log (Requirement 8.1) ---------------------
        # Use INFO for all non-error responses; ERROR for 5xx (except
        # the 500 branch above which is handled by the exception path).
        numeric_level = logging.ERROR if response.status_code >= 500 else logging.INFO

        if _should_emit(numeric_level):
            entry = {
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            }
            try:
                print(json.dumps(entry), flush=True)
            except Exception:
                pass  # silently discard on stdout failure

        return response
