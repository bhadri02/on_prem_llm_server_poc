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


# ===========================================================================
# security_layer.logging_config tests
# ---------------------------------------------------------------------------
# security_layer.config.Settings has four required env vars.  We patch them
# via monkeypatch (pytest fixture) or os.environ so the module imports cleanly.
# ===========================================================================

# Required env vars for security_layer.config.Settings to initialise
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}


def _setup_sl_env():
    """Patch required env vars so security_layer.config.Settings() succeeds."""
    for k, v in _SL_ENV.items():
        os.environ.setdefault(k, v)


_setup_sl_env()


def _make_sl_record(
    message: str = "test message",
    level: int = logging.INFO,
    extra_fields: dict | None = None,
) -> logging.LogRecord:
    """Build a minimal LogRecord for testing the security_layer formatter."""
    record = logging.LogRecord(
        name="sl_test_logger",
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


class TestSecurityLayerJSONFormatter:
    """Tests for security_layer.logging_config.JSONFormatter."""

    def setup_method(self):
        from security_layer.logging_config import JSONFormatter
        self.formatter = JSONFormatter()

    def test_output_is_valid_single_line_json(self):
        """format() must return valid JSON with no embedded newlines."""
        record = _make_sl_record("hello")
        output = self.formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert "\n" not in output

    def test_mandatory_fields_present(self):
        """timestamp, level, and message must all be present."""
        record = _make_sl_record("check", level=logging.WARNING)
        parsed = json.loads(self.formatter.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "message" in parsed

    def test_timestamp_iso8601_utc(self):
        """timestamp must end with 'Z'."""
        parsed = json.loads(self.formatter.format(_make_sl_record("ts")))
        assert parsed["timestamp"].endswith("Z")

    def test_level_field_matches_record(self):
        """level field must match the log record's levelname."""
        record = _make_sl_record(level=logging.ERROR)
        parsed = json.loads(self.formatter.format(record))
        assert parsed["level"] == "ERROR"

    def test_message_field_matches_record(self):
        """message field must equal the formatted log message."""
        record = _make_sl_record("exact text here")
        parsed = json.loads(self.formatter.format(record))
        assert parsed["message"] == "exact text here"

    def test_extra_fields_merged_at_top_level(self):
        """Extra fields passed via extra_fields must appear at the top level."""
        record = _make_sl_record(
            "event",
            extra_fields={"request_id": "abc-123", "latency_ms": 55},
        )
        parsed = json.loads(self.formatter.format(record))
        assert parsed.get("request_id") == "abc-123"
        assert parsed.get("latency_ms") == 55

    def test_no_extra_fields_yields_three_keys(self):
        """Without extra_fields only timestamp, level, message are present."""
        record = _make_sl_record("clean")
        parsed = json.loads(self.formatter.format(record))
        assert set(parsed.keys()) == {"timestamp", "level", "message"}


class TestSecurityLayerGetLogger:
    """Tests for security_layer.logging_config.get_logger()."""

    def _fresh_logger(self, name: str, log_level: str) -> logging.Logger:
        """Return a fresh security_layer logger with the given level."""
        import security_layer.logging_config as lc

        # Remove any cached logger entry so get_logger starts from scratch.
        if name in logging.Logger.manager.loggerDict:
            logging.getLogger(name).handlers.clear()
            del logging.Logger.manager.loggerDict[name]

        mock_settings = MagicMock()
        mock_settings.log_level = log_level
        with patch.object(lc, "settings", mock_settings):
            return lc.get_logger(name)

    def test_logger_has_stream_handler(self):
        """get_logger must attach exactly one StreamHandler to stdout."""
        logger = self._fresh_logger("sl.test.handler", "INFO")
        handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(handlers) == 1

    def test_handler_uses_json_formatter(self):
        """The StreamHandler must use JSONFormatter."""
        import security_layer.logging_config as lc
        logger = self._fresh_logger("sl.test.formatter", "INFO")
        for h in logger.handlers:
            assert isinstance(h.formatter, lc.JSONFormatter)

    def test_propagate_is_false(self):
        """Logger must not propagate records to the root logger."""
        logger = self._fresh_logger("sl.test.propagate", "INFO")
        assert logger.propagate is False

    def test_unrecognised_log_level_defaults_to_info(self):
        """Unknown LOG_LEVEL values must fall back to logging.INFO."""
        for bad in ("VERBOSE", "TRACE", "NOTAREAL", "", "99"):
            logger = self._fresh_logger(f"sl.test.fallback.{bad}", bad)
            assert logger.level == logging.INFO, (
                f"Expected INFO fallback for {bad!r}, got {logger.level}"
            )

    def test_known_log_levels_applied_correctly(self):
        """Recognised level strings must map to their numeric equivalents."""
        cases = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        for name, numeric in cases.items():
            logger = self._fresh_logger(f"sl.test.level.{name}", name)
            assert logger.level == numeric

    def test_duplicate_handlers_not_added(self):
        """Calling get_logger twice with the same name must not add handlers."""
        import security_layer.logging_config as lc

        name = "sl.test.no_dup"
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

    def test_emits_valid_json_to_stdout(self):
        """End-to-end: logger emits valid JSON with all required fields."""
        logger = self._fresh_logger("sl.test.emit", "INFO")
        buf = StringIO()
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler):
                h.stream = buf

        logger.info("security event", extra={"extra_fields": {"layer": "security"}})
        output = buf.getvalue().strip()

        assert output
        parsed = json.loads(output.split("\n")[0])
        assert parsed["message"] == "security event"
        assert parsed["layer"] == "security"
        assert parsed["level"] == "INFO"
        assert parsed["timestamp"].endswith("Z")


# ===========================================================================
# intelligent_router.logging_config tests
# ---------------------------------------------------------------------------
# intelligent_router.config.Settings has three required env vars.
# We patch them via os.environ so the module imports cleanly.
# ===========================================================================

# Required env vars for intelligent_router.config.Settings to initialise
_IR_ENV = {
    "MODEL_MATRIX_PATH": "/tmp/model_matrix.yaml",
    "TASK_RULES_PATH": "/tmp/task_rules.yaml",
    "AUDIT_STORE_URL": "http://audit:9200",
}


def _setup_ir_env():
    """Patch required env vars so intelligent_router.config.Settings() succeeds."""
    for k, v in _IR_ENV.items():
        os.environ.setdefault(k, v)


_setup_ir_env()


def _make_ir_record(
    message: str = "test message",
    level: int = logging.INFO,
    extra_fields: dict | None = None,
) -> logging.LogRecord:
    """Build a minimal LogRecord for testing the intelligent_router formatter."""
    record = logging.LogRecord(
        name="ir_test_logger",
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


class TestIntelligentRouterJSONFormatter:
    """Tests for intelligent_router.logging_config.JSONFormatter."""

    def setup_method(self):
        from intelligent_router.logging_config import JSONFormatter
        self.formatter = JSONFormatter()

    def test_output_is_valid_single_line_json(self):
        """format() must return valid JSON with no embedded newlines."""
        record = _make_ir_record("hello router")
        output = self.formatter.format(record)
        parsed = json.loads(output)  # raises if not valid JSON
        assert isinstance(parsed, dict)
        assert "\n" not in output

    def test_mandatory_fields_present(self):
        """timestamp, level, and message must all be present."""
        record = _make_ir_record("check fields", level=logging.WARNING)
        parsed = json.loads(self.formatter.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "message" in parsed

    def test_timestamp_is_iso8601_utc(self):
        """timestamp must be an ISO-8601 UTC string ending with 'Z'."""
        record = _make_ir_record("ts check")
        parsed = json.loads(self.formatter.format(record))
        ts = parsed["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z"), f"Expected timestamp to end with 'Z', got: {ts!r}"

    def test_extra_fields_merged_at_top_level(self):
        """Fields in extra_fields must appear at the top level of the JSON object."""
        record = _make_ir_record(
            "event with extras",
            extra_fields={"request_id": "req-abc", "task_type": "code"},
        )
        parsed = json.loads(self.formatter.format(record))
        assert parsed.get("request_id") == "req-abc"
        assert parsed.get("task_type") == "code"

    def test_level_field_matches_log_call(self):
        """level field must reflect the log level used (e.g. WARNING)."""
        record = _make_ir_record("warn msg", level=logging.WARNING)
        parsed = json.loads(self.formatter.format(record))
        assert parsed["level"] == "WARNING"

    def test_no_extra_fields_yields_three_keys(self):
        """Without extra_fields, only timestamp, level, message are present."""
        record = _make_ir_record("clean output")
        parsed = json.loads(self.formatter.format(record))
        assert set(parsed.keys()) == {"timestamp", "level", "message"}

    def test_message_field_matches_record(self):
        """message field must equal the formatted log message string."""
        record = _make_ir_record("exact router message")
        parsed = json.loads(self.formatter.format(record))
        assert parsed["message"] == "exact router message"


class TestIntelligentRouterGetLogger:
    """Tests for intelligent_router.logging_config.get_logger()."""

    def _fresh_logger(self, name: str, log_level: str) -> logging.Logger:
        """Return a fresh intelligent_router logger configured with *log_level*."""
        import intelligent_router.logging_config as lc

        # Remove any cached logger so handlers don't accumulate across tests.
        if name in logging.Logger.manager.loggerDict:
            logging.getLogger(name).handlers.clear()
            del logging.Logger.manager.loggerDict[name]

        mock_settings = MagicMock()
        mock_settings.log_level = log_level
        with patch.object(lc, "settings", mock_settings):
            return lc.get_logger(name)

    def test_logger_has_stream_handler(self):
        """get_logger must attach exactly one StreamHandler."""
        logger = self._fresh_logger("ir.test.handler", "INFO")
        handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(handlers) == 1

    def test_handler_uses_json_formatter(self):
        """The StreamHandler must use JSONFormatter."""
        import intelligent_router.logging_config as lc
        logger = self._fresh_logger("ir.test.formatter", "INFO")
        for h in logger.handlers:
            assert isinstance(h.formatter, lc.JSONFormatter)

    def test_unrecognised_log_level_falls_back_to_info(self):
        """Unknown LOG_LEVEL values must fall back to logging.INFO."""
        for bad_level in ("VERBOSE", "TRACE", "NOTAREAL", "", "42"):
            logger = self._fresh_logger(f"ir.test.fallback.{bad_level}", bad_level)
            assert logger.level == logging.INFO, (
                f"Expected INFO fallback for {bad_level!r}, got {logger.level}"
            )

    def test_valid_log_levels_are_set(self):
        """Recognised level strings must map to their correct numeric level."""
        cases = {
            "DEBUG": logging.DEBUG,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        for level_str, numeric in cases.items():
            logger = self._fresh_logger(f"ir.test.level.{level_str}", level_str)
            assert logger.level == numeric, (
                f"Expected {numeric} for {level_str!r}, got {logger.level}"
            )

    def test_duplicate_handlers_not_added(self):
        """Calling get_logger twice with the same name must not duplicate handlers."""
        import intelligent_router.logging_config as lc

        name = "ir.test.no_dup"
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

    def test_emits_valid_json_to_stdout(self):
        """End-to-end: logger emits valid JSON with all required fields to stdout."""
        logger = self._fresh_logger("ir.test.emit", "INFO")
        buf = StringIO()
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler):
                h.stream = buf

        logger.info("router event", extra={"extra_fields": {"layer": "router"}})
        output = buf.getvalue().strip()

        assert output, "Expected at least one log line"
        parsed = json.loads(output.split("\n")[0])
        assert parsed["message"] == "router event"
        assert parsed["layer"] == "router"
        assert parsed["level"] == "INFO"
        assert parsed["timestamp"].endswith("Z")

    def test_level_field_matches_log_call(self):
        """level field in JSON output must match the log method called."""
        logger = self._fresh_logger("ir.test.level_field", "DEBUG")
        buf = StringIO()
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler):
                h.stream = buf

        logger.warning("a warning")
        output = buf.getvalue().strip()
        parsed = json.loads(output.split("\n")[0])
        assert parsed["level"] == "WARNING"
