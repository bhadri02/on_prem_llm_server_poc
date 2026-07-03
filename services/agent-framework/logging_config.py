"""
services/agent-framework/logging_config.py

Structured JSON logger factory for the Agent Framework (Layer 6).

Provides:
  - JSONFormatter: a logging.Formatter subclass that emits single-line JSON
    with mandatory fields (timestamp, level, message) plus any extra fields
    passed via extra={"extra_fields": {...}}.
  - get_logger(name): factory that returns a configured Logger writing to
    sys.stdout, with the log level set from settings.log_level.

Log level behaviour (Requirement 12.3, 12.4):
  - Accepted values: DEBUG, INFO, WARNING, ERROR (case-insensitive)
  - Unrecognised values fall back to INFO and emit exactly ONE WARNING record
    indicating the unrecognised value and the fallback level.
  - Level ordering: DEBUG < INFO < WARNING < ERROR
"""

import json
import logging
import sys
from datetime import datetime, timezone

from agent_framework.config import settings

# Track which logger names have already emitted the "unrecognised level" warning
# so the warning is emitted only once per process lifetime (not per get_logger call).
_warned_loggers: set[str] = set()


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
      back to ``INFO`` and emit exactly ONE WARNING record.
    - Propagation to the root logger is disabled to prevent duplicate output.

    Args:
        name: The logger name (typically ``__name__`` of the calling module).

    Returns:
        A :class:`logging.Logger` instance ready for use.
    """
    logger = logging.getLogger(name)

    # Resolve log level; default to INFO for any unrecognised string.
    raw_level_str = (settings.log_level if settings is not None else "INFO")
    level_str = raw_level_str.upper()
    _VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
    level_unrecognised = level_str not in _VALID_LEVELS

    level = getattr(logging, level_str, None)
    if not isinstance(level, int):
        level = logging.INFO

    # Guard against duplicate handler accumulation across repeated calls.
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    logger.setLevel(level)

    # Emit exactly ONE WARNING for unrecognised log level values (Req 12.4).
    if level_unrecognised and name not in _warned_loggers:
        _warned_loggers.add(name)
        logger.warning(
            "Unrecognised LOG_LEVEL value %r; falling back to INFO",
            raw_level_str,
            extra={
                "extra_fields": {
                    "unrecognised_level": raw_level_str,
                    "fallback_level": "INFO",
                }
            },
        )

    return logger
