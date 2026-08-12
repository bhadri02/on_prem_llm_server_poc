"""
Structured JSON logging middleware for the API Gateway (Layer 1).

Delegates all logging to the shared observability module
(``shared.observability.logging``). Emits one structured JSON line per request
to stdout conforming to the mandatory Log_Schema.

request_id derivation:
    Extracted from the ``X-Request-ID`` header; falls back to a freshly
    generated UUID-v4 when the header is absent (virtually always, for real
    clients — a literal "none" fallback here used to mean AuthMiddleware's
    and RateLimitMiddleware's own audit events never correlated with the
    same request's request_received/response_sent events downstream, and
    "none" isn't a valid request_id for the Audit Store's UUID-v4
    validator either). The value is stored on ``request.state`` for
    downstream handlers (e.g. the exception handler in main.py, and
    build_imf() — see normalizer.py) to reuse rather than generate their own.

Sensitive data safety:
    - ``request.body()`` is NEVER called or read.
    - No IMF ``request.messages[].content`` values are passed to ``emit()``.
    - No auth header values (Authorization, X-API-Key, etc.) are passed to
      ``emit()``.
    - Only safe, structural fields are logged: method, path, status_code,
      latency_ms.

Unhandled exceptions during call_next are caught, logged at ERROR level, and
a JSON 500 response is returned so the caller always receives a well-formed
error body.

Validates: Requirements 6.1–6.6, 7.1–7.4
"""

from __future__ import annotations

import traceback as tb_module
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import time

from shared.observability.logging import emit, get_logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Emit one structured JSON log line to stdout per HTTP request.

    Uses :func:`shared.observability.logging.get_logger` and
    :func:`shared.observability.logging.emit` to produce schema-compliant
    structured log entries.

    Safe fields logged per request:
        request_id  — from ``X-Request-ID`` header (or a generated UUID-v4)
        method      — HTTP method
        path        — URL path (not query string)
        status_code — integer HTTP response status code
        latency_ms  — elapsed time in milliseconds

    Requirements 7.1–7.4 compliance:
        - ``request.body()`` is never read.
        - No IMF content, PII, or credential values are passed to ``emit()``.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Extract request_id from the dedicated header only (Req 7.3 — no other
        # header values are read for logging purposes); generate one if absent
        # so every request has a real, correlatable, Audit-Store-valid id.
        request_id: str = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Propagate request_id to request.state so downstream handlers can use it.
        request.state.request_id = request_id

        logger = get_logger(request_id)

        start = time.monotonic()

        # Call the next handler, catching any unhandled exceptions.
        try:
            response = await call_next(request)
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)

            emit(
                logger,
                level="ERROR",
                event="request_unhandled_exception",
                message=f"{request.method} {request.url.path} → unhandled exception: {type(exc).__name__}",
                latency_ms=latency_ms,
                exception_type=type(exc).__name__,
                # NOTE: exc message may contain sensitive data in theory; for POC
                # we include the type only in event and keep message to type name.
                # The traceback is intentionally omitted from emit() to avoid
                # accidentally leaking request body content that may appear in
                # tracebacks. It is only available in the process stderr.
            )

            return JSONResponse(
                status_code=500,
                content={"error": {"code": "500", "message": "Internal server error"}},
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        # Determine log level from HTTP status code.
        status_code = response.status_code
        if status_code >= 500:
            level = "ERROR"
        elif status_code >= 400:
            level = "WARN"
        else:
            level = "INFO"

        # Emit structured log entry with only safe fields (Req 7.1–7.4).
        # Deliberately NOT logging: request body, headers (other than request_id
        # already bound to logger), query params, or any IMF content fields.
        emit(
            logger,
            level=level,
            event="request_processed",
            message=f"{request.method} {request.url.path} → {status_code}",
            latency_ms=latency_ms,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
        )

        return response
