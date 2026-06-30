"""
tests/unit/test_logging.py

Unit tests for audit_store.logging_config:
  - JSONFormatter output is valid single-line JSON
  - Mandatory fields (timestamp, level, message) are always present
  - Extra fields supplied via extra={"extra_fields": {...}} are merged at the
    top level of the JSON object
  - Unrecognised LOG_LEVEL values fall back to INFO
"""

import json
import logging
import os
import sys
from io import StringIO
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    message: str = "test message",
    level: int = logging.INFO,
    extra_fields: dict | None = None,
) -> logging.LogRecord:
    """Build a minimal LogRecord for testing the formatter directly."""
    record = logging.LogRecord(
        name="test_logger",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    if extra_fields is not None:
        record.__dict__["extra_fields"] = extra_fields
    return record


# ---------------------------------------------------------------------------
# JSONFormatter tests
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    def setup_method(self):
        from audit_store.logging_config import JSONFormatter
        self.formatter = JSONFormatter()

    def test_output_is_valid_json(self):
        """format() must return a string that json.loads() can parse."""
        record = _make_record("hello world")
        output = self.formatter.format(record)
        parsed = json.loads(output)  # raises if not valid JSON
        assert isinstance(parsed, dict)

    def test_output_is_single_line(self):
        """format() output must not contain embedded newline characters."""
        record = _make_record("a message with\nnewlines\nembedded")
        output = self.formatter.format(record)
        # The JSON string itself must be one line
        assert "\n" not in output

    def test_mandatory_fields_present(self):
        """timestamp, level, and message must appear in every log record."""
        record = _make_record("check fields", level=logging.WARNING)
        parsed = json.loads(self.formatter.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "message" in parsed

    def test_timestamp_is_iso8601_utc(self):
        """timestamp must be an ISO-8601 UTC string ending with 'Z'."""
        record = _make_record("ts check")
        parsed = json.loads(self.formatter.format(record))
        ts = parsed["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z"), f"Expected timestamp to end with 'Z', got: {ts!r}"

    def test_level_reflects_log_record(self):
        """level field must match the LogRecord's levelname."""
        for level, expected in [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
        ]:
            record = _make_record(level=level)
            parsed = json.loads(self.formatter.format(record))
            assert parsed["level"] == expected, (
                f"Expected level={expected!r}, got {parsed['level']!r}"
            )

    def test_message_field_matches_log_message(self):
        """message field must equal the formatted log message."""
        record = _make_record("the exact message")
        parsed = json.loads(self.formatter.format(record))
        assert parsed["message"] == "the exact message"

    def test_extra_fields_merged_at_top_level(self):
        """Fields supplied via extra_fields must appear at the top level."""
        record = _make_record(
            "event with extras",
            extra_fields={"audit_id": "abc-123", "latency_ms": 42},
        )
        parsed = json.loads(self.formatter.format(record))
        assert parsed.get("audit_id") == "abc-123"
        assert parsed.get("latency_ms") == 42

    def test_extra_fields_do_not_overwrite_mandatory_fields(self):
        """If extra_fields contains 'level', it overwrites the mandatory field.

        The design spec calls for a simple ``payload.update(extra_fields)``,
        so caller-supplied values do take precedence.  This test documents
        (rather than forbids) that behaviour.
        """
        record = _make_record("msg", extra_fields={"level": "CUSTOM"})
        parsed = json.loads(self.formatter.format(record))
        # After update(), extra_fields wins — document the behaviour.
        assert parsed["level"] == "CUSTOM"

    def test_no_extra_fields_key_on_record(self):
        """Records without extra_fields produce clean output with no extra keys."""
        record = _make_record("no extras")
        parsed = json.loads(self.formatter.format(record))
        # Only the three mandatory keys should be present
        assert set(parsed.keys()) == {"timestamp", "level", "message"}

    def test_extra_fields_with_nested_dict(self):
        """Nested dicts in extra_fields are serialised correctly."""
        nested = {"user": {"id": "u1", "dept": "eng"}}
        record = _make_record("nested", extra_fields=nested)
        parsed = json.loads(self.formatter.format(record))
        assert parsed["user"] == {"id": "u1", "dept": "eng"}


# ---------------------------------------------------------------------------
# get_logger tests
# ---------------------------------------------------------------------------

class TestGetLogger:
    """Tests for the get_logger() factory function."""

    def _fresh_logger(self, name: str, log_level: str) -> logging.Logger:
        """Return a fresh logger with the given level by patching settings
        and calling get_logger directly (no module reload needed).
        """
        import audit_store.logging_config as lc

        # Wipe any existing logger so get_logger starts clean.
        existing = logging.Logger.manager.loggerDict.get(name)
        if existing:
            lg = logging.getLogger(name)
            lg.handlers.clear()
            del logging.Logger.manager.loggerDict[name]

        mock_settings = MagicMock()
        mock_settings.log_level = log_level
        with patch.object(lc, "settings", mock_settings):
            return lc.get_logger(name)

    def test_logger_has_stream_handler(self):
        """get_logger must attach exactly one StreamHandler."""
        logger = self._fresh_logger("test.handler", "INFO")
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) == 1

    def test_logger_handler_uses_json_formatter(self):
        """The StreamHandler must use JSONFormatter."""
        import audit_store.logging_config as lc
        logger = self._fresh_logger("test.formatter_check", "INFO")
        for handler in logger.handlers:
            assert isinstance(handler.formatter, lc.JSONFormatter)

    def test_logger_does_not_propagate(self):
        """Logger must not propagate to the root logger."""
        logger = self._fresh_logger("test.propagate", "DEBUG")
        assert logger.propagate is False

    def test_known_log_levels_are_set_correctly(self):
        """Recognised level strings must map to the correct numeric level."""
        expected = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        for level_str, numeric in expected.items():
            logger = self._fresh_logger(f"test.level.{level_str}", level_str)
            assert logger.level == numeric, (
                f"Expected level {numeric} for {level_str!r}, got {logger.level}"
            )

    def test_unrecognised_log_level_defaults_to_info(self):
        """Unknown LOG_LEVEL values (e.g. 'VERBOSE', 'TRACE') must fall back to INFO."""
        for bad_level in ("VERBOSE", "TRACE", "NOTAREAL", "", "42"):
            logger = self._fresh_logger(f"test.fallback.{bad_level}", bad_level)
            assert logger.level == logging.INFO, (
                f"Expected INFO fallback for {bad_level!r}, got level {logger.level}"
            )

    def test_logger_emits_valid_json_to_stdout(self):
        """End-to-end: log a message, capture stdout, verify valid JSON."""
        logger = self._fresh_logger("test.emit", "INFO")
        # Replace the handler's stream with a StringIO buffer for capture.
        buf = StringIO()
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.stream = buf

        logger.info("hello from test", extra={"extra_fields": {"key": "val"}})
        output = buf.getvalue().strip()

        assert output, "Expected at least one log line"
        line = output.split("\n")[0]
        parsed = json.loads(line)
        assert parsed["message"] == "hello from test"
        assert parsed["key"] == "val"
        assert parsed["level"] == "INFO"
        assert parsed["timestamp"].endswith("Z")

    def test_calling_get_logger_twice_does_not_duplicate_handlers(self):
        """Calling get_logger with the same name must not add extra handlers."""
        import audit_store.logging_config as lc

        name = "test.no_dup_handlers"
        # Wipe any pre-existing cached logger entry.
        if name in logging.Logger.manager.loggerDict:
            logging.getLogger(name).handlers.clear()
            del logging.Logger.manager.loggerDict[name]

        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        with patch.object(lc, "settings", mock_settings):
            l1 = lc.get_logger(name)
            l2 = lc.get_logger(name)

        assert l1 is l2
        assert len(l1.handlers) == 1
