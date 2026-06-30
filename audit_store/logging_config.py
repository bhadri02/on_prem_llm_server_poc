"""
logging_config.py — Structured JSON logger for the Audit Store service.

Provides a JSONFormatter that emits single-line JSON log records and a
get_logger() factory that wires it up to stdout with the configured log level.
"""

import json
import logging
import sys
import datetime

from audit_store.config import settings


class JSONFormatter(logging.Formatter):
    """Custom log formatter that produces single-line JSON log records.

    Each record includes at minimum:
      - ``timestamp``: ISO-8601 UTC string ending with "Z"
      - ``level``: the log level name (e.g. "INFO", "ERROR")
      - ``message``: the log message

    Callers may pass extra structured fields via::

        logger.info("some_event", extra={"extra_fields": {"key": "value"}})

    All key/value pairs in ``extra_fields`` are merged at the top level of
    the JSON object.
    """

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: dict = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Merge any caller-supplied extra fields at the top level.
        extra_fields = record.__dict__.get("extra_fields", {})
        if extra_fields:
            payload.update(extra_fields)
        # json.dumps produces a single line (no embedded newlines by default).
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    """Create (or retrieve) a named logger with JSON stdout output.

    The log level is read from ``settings.log_level``.  If the configured
    value is not a recognised Python logging level name, the logger falls back
    to ``INFO`` so the service always starts correctly.

    The logger does **not** propagate to the root logger, which prevents
    duplicate output when both the module logger and the root logger have
    handlers attached.

    Args:
        name: The logger name (typically ``__name__`` of the calling module).

    Returns:
        A :class:`logging.Logger` instance ready for use.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if the logger was already configured.
    if logger.handlers:
        return logger

    # Resolve the configured level, defaulting to INFO for unknown values.
    level_name: str = settings.log_level.upper()
    level: int = getattr(logging, level_name, None)  # type: ignore[arg-type]
    if not isinstance(level, int):
        level = logging.INFO

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)

    # Prevent records from bubbling up to the root logger.
    logger.propagate = False

    return logger
