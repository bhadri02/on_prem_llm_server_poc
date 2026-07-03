"""
shared.observability.logging — structured JSON logging via structlog.

Provides:
- `configure_structlog()` — global structlog setup per service
- `get_logger()`          — request-scoped logger pre-bound with request_id
- `emit()`                — schema-enforced log emission (never raises)

All log entries conform to the mandatory Log_Schema:
  { timestamp, level, service, request_id, event, message, [latency_ms], data }

Implementation: task 3.1
Property tests:  task 3.2 (Property 5), task 3.3 (Property 6),
                 task 3.4 (Property 7), task 3.5 (unit edge cases)
Requirements: 6.1–6.6, 7.1–7.4
"""

from __future__ import annotations

import os
import sys
import structlog
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "configure_structlog",
    "get_logger",
    "emit",
]

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_SERVICE: str = "unknown"
_LOG_LEVEL: str = "INFO"

# Numeric ordering for level filtering: DEBUG < INFO < WARN < ERROR
_LEVEL_ORDER: dict[str, int] = {
    "DEBUG": 0,
    "INFO": 1,
    "WARN": 2,
    "ERROR": 3,
}

# Map structlog method names: WARN → warning
_LEVEL_TO_METHOD: dict[str, str] = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARN": "warning",
    "ERROR": "error",
}

_VALID_LEVELS = frozenset(_LEVEL_ORDER.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso_now() -> str:
    """Return the current UTC time as ISO-8601 with millisecond precision and Z suffix."""
    now = datetime.now(timezone.utc)
    # Format: 2024-06-01T12:00:00.000Z
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _resolve_level(log_level: str) -> str:
    """Normalise and validate a log level string.

    Returns the upper-cased level if valid, else "INFO".
    Does NOT emit any warning here — the caller handles that.
    """
    return log_level.upper() if log_level.upper() in _VALID_LEVELS else "INFO"


def _schema_order_processor(logger: Any, method: str, event_dict: dict) -> dict:
    """Reorder event_dict keys to match the Log_Schema field order.

    Log_Schema order:
      timestamp, level, service, request_id, event, message,
      [latency_ms], data, <any remaining keys>
    """
    ordered: dict[str, Any] = {}

    for key in ("timestamp", "level", "service", "request_id", "event", "message"):
        if key in event_dict:
            ordered[key] = event_dict.pop(key)

    # latency_ms only when present
    if "latency_ms" in event_dict:
        ordered["latency_ms"] = event_dict.pop("latency_ms")

    # data field
    if "data" in event_dict:
        ordered["data"] = event_dict.pop("data")

    # any remaining keys
    ordered.update(event_dict)

    return ordered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_structlog(service: str, log_level: str = "INFO") -> None:
    """Configure structlog globally for a platform service.

    If ``log_level`` is the default ``"INFO"``, reads ``LOG_LEVEL`` from the
    environment; falls back to ``"INFO"`` when the env var is absent.

    For unrecognised level values, falls back to ``"INFO"`` and emits a
    ``WARN``-level log entry with ``event="invalid_log_level"``.

    Args:
        service:   The service/layer name written into every log entry.
        log_level: One of ``"DEBUG"``, ``"INFO"``, ``"WARN"``, ``"ERROR"``.
                   Pass the default ``"INFO"`` to defer to the ``LOG_LEVEL``
                   env var.
    """
    global _SERVICE, _LOG_LEVEL

    _SERVICE = service

    # Resolve the effective level: env var wins when caller passes the default.
    raw_level = log_level
    if log_level == "INFO":
        raw_level = os.environ.get("LOG_LEVEL", "INFO")

    resolved = raw_level.upper()
    invalid = resolved not in _VALID_LEVELS
    if invalid:
        resolved = "INFO"

    _LOG_LEVEL = resolved

    # Configure structlog with a processor chain that produces
    # single-line JSON to stdout with Log_Schema field order.
    structlog.configure(
        processors=[
            _schema_order_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )

    # Emit the invalid-level warning after configuration is set up so that
    # the warning itself is valid structured JSON.
    if invalid:
        _warn_logger = structlog.get_logger().bind(
            service=_SERVICE,
            request_id="none",
            timestamp=_utc_iso_now(),
        )
        _warn_logger.warning(
            event="invalid_log_level",
            level="WARN",
            message=f"Unrecognised log level {raw_level!r}; falling back to INFO",
            data={"invalid_value": raw_level},
        )


def get_logger(request_id: str = "none") -> structlog.BoundLogger:
    """Return a structlog BoundLogger pre-bound with ``request_id`` and ``timestamp``.

    The ``timestamp`` is the UTC ISO-8601 time at the moment ``get_logger()``
    is called, with millisecond precision and ``Z`` suffix.

    Args:
        request_id: The UUID-v4 request identifier, or ``"none"`` for
                    non-request-scoped events (startup, config load, etc.).

    Returns:
        A :class:`structlog.BoundLogger` with ``request_id`` and ``timestamp``
        already bound.
    """
    return structlog.get_logger().bind(
        request_id=request_id,
        timestamp=_utc_iso_now(),
    )


def emit(
    logger: structlog.BoundLogger,
    level: str,
    event: str,
    message: str,
    latency_ms: int | None = None,
    **data: Any,
) -> None:
    """Emit a single structured log entry conforming to the Log_Schema.

    This function **never raises** — any internal exception is silently
    swallowed so that a logging failure can never crash a service.

    Field order in the emitted JSON follows the Log_Schema:
    ``timestamp``, ``level``, ``service``, ``request_id``, ``event``,
    ``message``, ``latency_ms`` (only when not ``None``), ``data``.

    Args:
        logger:     A bound logger returned by :func:`get_logger`.
        level:      One of ``"DEBUG"``, ``"INFO"``, ``"WARN"``, ``"ERROR"``.
                    Defaults to ``"INFO"`` for unrecognised values.
        event:      Snake_case machine-readable event identifier.
        message:    Human-readable string (max 256 chars; truncated if longer).
        latency_ms: Request latency in milliseconds.  Omitted from output
                    entirely when ``None``.
        **data:     Additional structured context.  Callers are responsible
                    for never passing sensitive fields here.
    """
    try:
        # ── Validate / normalise level ────────────────────────────────────
        normalised_level = level.upper() if isinstance(level, str) else "INFO"
        if normalised_level not in _VALID_LEVELS:
            normalised_level = "INFO"

        # ── Apply level filtering ─────────────────────────────────────────
        if _LEVEL_ORDER.get(normalised_level, 1) < _LEVEL_ORDER.get(_LOG_LEVEL, 1):
            return  # below configured threshold — suppress

        # ── Validate / truncate message ───────────────────────────────────
        if not isinstance(message, str):
            message = str(message)
        if len(message) > 256:
            message = message[:255] + "..."

        # ── Build the ordered kwargs dict (Log_Schema order) ─────────────
        # structlog treats the first positional arg as `event`.
        # We pass the remaining schema fields as kwargs so the processor
        # can reorder them into Log_Schema order.
        kwargs: dict[str, Any] = {
            "level": normalised_level,
            "service": _SERVICE,
            "message": message,
        }

        if latency_ms is not None:
            kwargs["latency_ms"] = latency_ms

        kwargs["data"] = data

        # ── Dispatch to the correct structlog method ──────────────────────
        # structlog places the first positional arg as `event` in the dict.
        method_name = _LEVEL_TO_METHOD[normalised_level]
        log_fn = getattr(logger, method_name)
        log_fn(event, **kwargs)

    except Exception:
        # Logging must never crash the service.
        pass
