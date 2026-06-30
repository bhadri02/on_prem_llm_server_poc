"""
Structured JSON logging middleware for the Cache Service (Layer 4).

Implements LoggingMiddleware(BaseHTTPMiddleware) that emits one JSON log line
to stdout per HTTP request, containing: timestamp (ISO-8601 UTC + "Z"), level
(INFO for status < 500, ERROR for status >= 500), method, path, status_code,
latency_ms (rounded to 2 dp), and request_id.

request_id extraction priority:
  1. IMF body field `request_id` (parsed from JSON body)
  2. `X-Request-ID` request header
  3. Falls back to "unknown"

PII safety rules (Requirements 7.6):
  - Never log any key whose name appears in `governance.pii_fields_detected`.
  - Never log the raw string value of any `request.messages[].content` field.

Respects LOG_LEVEL from get_settings(); invalid values treated as "INFO".
If stdout write raises any exception: silently discards the entry and continues.

Validates: Requirements 6.6, 7.5, 7.6, 7.7
"""

import json
import time
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from cache_service.config import get_settings

# Numeric log level priorities matching Python's logging module conventions.
_LEVEL_PRIORITY: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,      # alias
    "ERROR": 40,
    "CRITICAL": 50,
}


def _should_emit(level: str) -> bool:
    """Return True if *level* meets or exceeds the configured LOG_LEVEL.

    Rules (per Requirements 7.5):
    - If LOG_LEVEL is WARNING or higher, INFO entries are suppressed.
    - ERROR entries are always emitted regardless of configured level.
    - Unknown LOG_LEVEL values default to INFO priority (20).
    """
    # ERROR is always emitted — hard requirement 7.5
    if level == "ERROR":
        return True

    settings = get_settings()
    configured = settings.log_level.upper()
    configured_priority = _LEVEL_PRIORITY.get(configured, _LEVEL_PRIORITY["INFO"])
    entry_priority = _LEVEL_PRIORITY.get(level.upper(), _LEVEL_PRIORITY["INFO"])

    return entry_priority >= configured_priority


def _extract_pii_fields(body: dict[str, Any]) -> set[str]:
    """Extract the list of PII field names from governance.pii_fields_detected.

    Returns an empty set if the body is not a dict or the field is absent/non-list.
    """
    try:
        governance = body.get("governance", {})
        if not isinstance(governance, dict):
            return set()
        pii_fields = governance.get("pii_fields_detected", [])
        if not isinstance(pii_fields, list):
            return set()
        return {str(f) for f in pii_fields if isinstance(f, str)}
    except Exception:
        return set()


def _extract_message_content_values(body: dict[str, Any]) -> set[str]:
    """Extract all raw content string values from request.messages[].content.

    Returns an empty set if the body is not a dict or messages is absent/malformed.
    """
    content_values: set[str] = set()
    try:
        request_block = body.get("request", {})
        if not isinstance(request_block, dict):
            return content_values
        messages = request_block.get("messages", [])
        if not isinstance(messages, list):
            return content_values
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    content_values.add(content)
    except Exception:
        pass
    return content_values


class LoggingMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that emits one structured JSON log line per request.

    The log entry is written to stdout via ``print(..., flush=True)`` after
    the response is returned so that the status code and latency are both
    available.

    Log entry fields (Requirements 6.6, 7.5, 7.6, 7.7):
        timestamp   — ISO-8601 UTC datetime with "Z" suffix
        level       — "INFO" (status < 500) or "ERROR" (status >= 500)
        method      — HTTP method (GET, POST, …)
        path        — URL path component
        status_code — integer HTTP response status code
        latency_ms  — elapsed time in milliseconds, rounded to 2 decimal places
        request_id  — extracted from body JSON, X-Request-ID header, or "unknown"

    PII safety:
        - Keys listed in governance.pii_fields_detected are never included.
        - Raw request.messages[].content string values are never included.
    """

    async def dispatch(self, request: Request, call_next):
        # --- Read and re-inject the raw request body ---
        # We must consume the body now to extract request_id, but downstream
        # handlers also need to read it. Re-inject via request.scope["_body"]
        # so that subsequent await request.body() calls return the same bytes.
        body_bytes = b""
        body_dict: dict[str, Any] = {}
        try:
            body_bytes = await request.body()
        except Exception:
            body_bytes = b""

        # Re-inject body so downstream handlers can still read it
        request.scope["_body"] = body_bytes

        # Attempt to parse the body as JSON
        if body_bytes:
            try:
                body_dict = json.loads(body_bytes.decode("utf-8"))
                if not isinstance(body_dict, dict):
                    body_dict = {}
            except Exception:
                body_dict = {}

        # --- Extract request_id with priority order ---
        # 1. IMF body field
        request_id = body_dict.get("request_id") if body_dict else None
        if not isinstance(request_id, str) or not request_id:
            # 2. X-Request-ID header
            request_id = request.headers.get("X-Request-ID")
        if not request_id:
            # 3. Fallback
            request_id = "unknown"

        # --- Extract PII exclusion metadata before calling next ---
        pii_fields = _extract_pii_fields(body_dict)
        forbidden_content_values = _extract_message_content_values(body_dict)

        # --- Process the request ---
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = (time.monotonic() - start) * 1000

        level = "ERROR" if response.status_code >= 500 else "INFO"

        if _should_emit(level):
            entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "level": level,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
                "request_id": request_id,
            }

            # --- Apply PII exclusion (Requirement 7.6) ---
            # Remove any key whose name appears in governance.pii_fields_detected
            for pii_field in pii_fields:
                entry.pop(pii_field, None)

            # Remove any value that matches a raw messages[].content string
            # (scan entry values and redact matching strings)
            if forbidden_content_values:
                entry = {
                    k: v
                    for k, v in entry.items()
                    if not (isinstance(v, str) and v in forbidden_content_values)
                }

            # --- Emit to stdout; silently discard on any failure (Requirement 7.7) ---
            try:
                print(json.dumps(entry), flush=True)
            except Exception:
                pass  # silently discard

        return response
