"""
intelligent_router/logging_config.py

Structured JSON logger for the Intelligent Router (Layer 3).

Provides:
  - JSONFormatter: a logging.Formatter subclass that emits single-line JSON
    with mandatory fields (timestamp, level, message) plus any extra fields
    passed via extra={"extra_fields": {...}}.
  - get_logger(name): factory that returns a configured Logger writing to
    sys.stdout, with the log level set from settings.log_level (defaults to
    INFO for unrecognised values).

Note: `configure_structlog("router", ...)` is called in main.py at module
level to ensure the shared structlog is available globally (Requirements 6.1–6.6).
This module keeps its own JSONFormatter/get_logger for backward compatibility
with existing call-sites and tests.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from intelligent_router.config import settings


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON string.

    The output always contains:
      - ``timestamp`` — ISO-8601 UTC with millisecond precision, ending in "Z"
      - ``level``     — the record's levelname (e.g. "INFO", "WARNING")
      - ``message``   — the formatted log message

    Any additional fields in ``record.extra_fields`` (a dict) are merged into
    the top-level JSON object, allowing callers to pass structured context via::

        logger.info("event", extra={"extra_fields": {"request_id": "abc"}})
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            ),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields and isinstance(extra_fields, dict):
            log_entry.update(extra_fields)
        return json.dumps(log_entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Return a Logger named *name* configured for structured JSON output.

    - Attaches a ``StreamHandler(sys.stdout)`` using ``JSONFormatter``.
    - Handler is only added once; repeated calls with the same name are safe.
    - Log level comes from ``settings.log_level``; unrecognised values fall
      back to ``INFO``.
    - Propagation to the root logger is disabled to prevent duplicate output.
    """
    logger = logging.getLogger(name)

    # Guard against duplicate handler accumulation across repeated calls.
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    # Resolve log level; default to INFO for any unrecognised string.
    level_str = (settings.log_level if settings is not None else "INFO").upper()
    level = getattr(logging, level_str, None)
    if not isinstance(level, int):
        level = logging.INFO
    logger.setLevel(level)

    return logger
