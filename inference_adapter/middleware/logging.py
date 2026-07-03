"""
Structured JSON logging middleware for the Inference Adapter (Layer 5).

Delegates to the shared observability module's `LoggingMiddleware` so that all
log entries conform to the mandatory Log_Schema and structlog is used
consistently across every platform layer.

The shared middleware:
  - Emits one structured log entry per request using `get_logger()` and `emit()`
  - Extracts `request_id` from the `X-Request-ID` header only — never reads
    request.body() and never passes IMF content, PII, or credentials to `emit()`
  - Determines log level from HTTP status code (INFO / WARN / ERROR)

Requirements: 10.4, 10.5, 10.6
"""

from shared.observability.middleware import LoggingMiddleware  # noqa: F401 — re-exported

__all__ = ["LoggingMiddleware"]
