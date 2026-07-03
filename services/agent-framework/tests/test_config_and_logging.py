"""
tests/test_config_and_logging.py

Unit tests for agent_framework.config and agent_framework.logging_config.

Covers:
  - Settings fields map correctly from environment variables
  - LOG_LEVEL values DEBUG / INFO / WARNING / ERROR are accepted
  - Unrecognised LOG_LEVEL defaults to INFO and emits exactly one WARNING

Requirements: 12.3, 12.4
"""

import json
import logging
import sys
from io import StringIO
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSettings:
    """Verify that Settings fields read the correct environment variables."""

    def test_default_values(self):
        """Settings has correct defaults when no env vars are set."""
        from agent_framework.config import Settings

        s = Settings()
        assert s.router_url == "http://router:8082"
        assert s.gateway_api_key == "poc-secret-key"
        assert s.tool_catalog_path == "/config/tools/catalog.yaml"
        assert s.log_level == "INFO"
        assert s.max_agent_steps == 10
        assert s.port == 8083
        assert s.metrics_port == 9090
        assert s.agent_sub_call_timeout_seconds == 30.0

    def test_router_url_from_env(self, monkeypatch):
        """ROUTER_URL env var overrides router_url default."""
        monkeypatch.setenv("ROUTER_URL", "http://custom-router:9000")
        from agent_framework.config import Settings

        s = Settings()
        assert s.router_url == "http://custom-router:9000"

    def test_gateway_api_key_from_env(self, monkeypatch):
        """GATEWAY_API_KEY env var overrides gateway_api_key default."""
        monkeypatch.setenv("GATEWAY_API_KEY", "my-secret-key")
        from agent_framework.config import Settings

        s = Settings()
        assert s.gateway_api_key == "my-secret-key"

    def test_tool_catalog_path_from_env(self, monkeypatch):
        """TOOL_CATALOG_PATH env var overrides tool_catalog_path default."""
        monkeypatch.setenv("TOOL_CATALOG_PATH", "/custom/path/catalog.yaml")
        from agent_framework.config import Settings

        s = Settings()
        assert s.tool_catalog_path == "/custom/path/catalog.yaml"

    def test_log_level_from_env(self, monkeypatch):
        """LOG_LEVEL env var overrides log_level default."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        from agent_framework.config import Settings

        s = Settings()
        assert s.log_level == "DEBUG"

    def test_max_agent_steps_from_env(self, monkeypatch):
        """MAX_AGENT_STEPS env var overrides max_agent_steps default."""
        monkeypatch.setenv("MAX_AGENT_STEPS", "25")
        from agent_framework.config import Settings

        s = Settings()
        assert s.max_agent_steps == 25

    def test_port_from_env(self, monkeypatch):
        """PORT env var overrides port default."""
        monkeypatch.setenv("PORT", "9000")
        from agent_framework.config import Settings

        s = Settings()
        assert s.port == 9000

    def test_metrics_port_from_env(self, monkeypatch):
        """METRICS_PORT env var overrides metrics_port default."""
        monkeypatch.setenv("METRICS_PORT", "9091")
        from agent_framework.config import Settings

        s = Settings()
        assert s.metrics_port == 9091

    def test_agent_sub_call_timeout_from_env(self, monkeypatch):
        """AGENT_SUB_CALL_TIMEOUT_SECONDS env var overrides default."""
        monkeypatch.setenv("AGENT_SUB_CALL_TIMEOUT_SECONDS", "15.5")
        from agent_framework.config import Settings

        s = Settings()
        assert s.agent_sub_call_timeout_seconds == 15.5


# ---------------------------------------------------------------------------
# Logging tests
# ---------------------------------------------------------------------------


def _capture_log_output(name: str, log_level_env: str) -> tuple[logging.Logger, list[str]]:
    """Helper: create a fresh logger with the given LOG_LEVEL env value.

    Returns (logger, list_of_captured_json_lines).
    """
    import importlib
    import agent_framework.logging_config as lc_mod

    # Reset module state so each test starts clean
    lc_mod._warned_loggers.discard(name)

    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(lc_mod.JSONFormatter())

    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    # Resolve level using the same logic as get_logger
    level_str = log_level_env.upper()
    level_unrecognised = level_str not in lc_mod._VALID_LEVELS
    level = lc_mod._VALID_LEVELS.get(level_str, logging.INFO)
    logger.setLevel(level)

    # Emit the warning if unrecognised and not yet warned
    if level_unrecognised and name not in lc_mod._warned_loggers:
        lc_mod._warned_loggers.add(name)
        logger.warning(
            "Unrecognised LOG_LEVEL value %r; falling back to INFO",
            log_level_env,
            extra={
                "extra_fields": {
                    "unrecognised_level": log_level_env,
                    "fallback_level": "INFO",
                }
            },
        )

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return logger, lines


class TestGetLoggerLevelAccepted:
    """Verify that recognised LOG_LEVEL values are accepted without warning."""

    @pytest.mark.parametrize("level_str,expected_level", [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
    ])
    def test_recognised_level_sets_logger_level(self, monkeypatch, level_str, expected_level):
        """Recognised LOG_LEVEL sets the correct logging level with no warning emitted."""
        import agent_framework.logging_config as lc_mod
        monkeypatch.setattr(lc_mod, "_warned_loggers", set())

        from agent_framework.config import Settings
        mock_settings = Settings()
        mock_settings.log_level = level_str
        monkeypatch.setattr(lc_mod, "settings", mock_settings)

        name = f"test_recognised_{level_str}"
        # Remove any leftover handlers
        log = logging.getLogger(name)
        log.handlers.clear()

        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            result_logger = lc_mod.get_logger(name)

        assert result_logger.level == expected_level

        # No warning should have been emitted (buf captures stdout output)
        output = buf.getvalue()
        # Parse any JSON lines and verify none are WARNING about unrecognised level
        for line in output.splitlines():
            if line.strip():
                record = json.loads(line)
                assert not (
                    record.get("level") == "WARNING"
                    and "Unrecognised" in record.get("message", "")
                ), f"Unexpected warning emitted for recognised level {level_str!r}"

    def test_debug_level_emits_debug_records(self, monkeypatch):
        """With LOG_LEVEL=DEBUG, DEBUG records are emitted."""
        import agent_framework.logging_config as lc_mod
        from agent_framework.config import Settings

        monkeypatch.setattr(lc_mod, "_warned_loggers", set())
        mock_settings = Settings()
        mock_settings.log_level = "DEBUG"
        monkeypatch.setattr(lc_mod, "settings", mock_settings)

        name = "test_debug_emission"
        log = logging.getLogger(name)
        log.handlers.clear()

        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            result_logger = lc_mod.get_logger(name)
            result_logger.debug("debug message")

        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert any(
            json.loads(ln).get("level") == "DEBUG" for ln in lines
        ), "Expected at least one DEBUG record"


class TestGetLoggerUnrecognisedLevel:
    """Verify unrecognised LOG_LEVEL falls back to INFO with exactly one WARNING."""

    @pytest.mark.parametrize("bad_level", ["VERBOSE", "TRACE", "CRITICAL", "OFF", "nonsense", "123"])
    def test_unrecognised_level_falls_back_to_info(self, monkeypatch, bad_level):
        """Unrecognised LOG_LEVEL falls back to INFO logging level."""
        import agent_framework.logging_config as lc_mod
        from agent_framework.config import Settings

        monkeypatch.setattr(lc_mod, "_warned_loggers", set())
        mock_settings = Settings()
        mock_settings.log_level = bad_level
        monkeypatch.setattr(lc_mod, "settings", mock_settings)

        name = f"test_fallback_{bad_level}"
        log = logging.getLogger(name)
        log.handlers.clear()

        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            result_logger = lc_mod.get_logger(name)

        # Level should be INFO
        assert result_logger.level == logging.INFO, (
            f"Expected INFO level for unrecognised {bad_level!r}, got {result_logger.level}"
        )

    @pytest.mark.parametrize("bad_level", ["VERBOSE", "TRACE", "CRITICAL"])
    def test_unrecognised_level_emits_exactly_one_warning(self, monkeypatch, bad_level):
        """Exactly one WARNING record is emitted for an unrecognised LOG_LEVEL."""
        import agent_framework.logging_config as lc_mod
        from agent_framework.config import Settings

        # Fresh warned_loggers set for each test
        monkeypatch.setattr(lc_mod, "_warned_loggers", set())
        mock_settings = Settings()
        mock_settings.log_level = bad_level
        monkeypatch.setattr(lc_mod, "settings", mock_settings)

        name = f"test_one_warning_{bad_level}"
        log = logging.getLogger(name)
        log.handlers.clear()

        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            # Call get_logger multiple times — warning should only appear once
            lc_mod.get_logger(name)
            lc_mod.get_logger(name)
            lc_mod.get_logger(name)

        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        warning_records = [
            json.loads(ln) for ln in lines
            if json.loads(ln).get("level") == "WARNING"
            and "Unrecognised" in json.loads(ln).get("message", "")
        ]
        assert len(warning_records) == 1, (
            f"Expected exactly 1 WARNING for unrecognised level {bad_level!r}, "
            f"got {len(warning_records)}: {warning_records}"
        )

    def test_unrecognised_level_warning_contains_value_and_fallback(self, monkeypatch):
        """The WARNING record contains the unrecognised value and the fallback level."""
        import agent_framework.logging_config as lc_mod
        from agent_framework.config import Settings

        bad_level = "MYWEIRDLEVEL"
        monkeypatch.setattr(lc_mod, "_warned_loggers", set())
        mock_settings = Settings()
        mock_settings.log_level = bad_level
        monkeypatch.setattr(lc_mod, "settings", mock_settings)

        name = "test_warning_content"
        log = logging.getLogger(name)
        log.handlers.clear()

        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            lc_mod.get_logger(name)

        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        warning_records = [
            json.loads(ln) for ln in lines
            if json.loads(ln).get("level") == "WARNING"
        ]
        assert len(warning_records) >= 1
        record = warning_records[0]
        # The unrecognised value should appear in the message or extra fields
        msg = record.get("message", "")
        assert bad_level in msg or record.get("unrecognised_level") == bad_level, (
            f"Unrecognised level value {bad_level!r} not found in warning record: {record}"
        )


class TestJSONFormatter:
    """Verify JSONFormatter emits well-formed JSON with required fields."""

    def test_json_formatter_has_required_fields(self):
        """JSON output includes timestamp, level, name, and message."""
        from agent_framework.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="mylogger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert "timestamp" in parsed
        assert "level" in parsed
        assert "name" in parsed
        assert "message" in parsed
        assert parsed["level"] == "INFO"
        assert parsed["name"] == "mylogger"
        assert parsed["message"] == "hello world"

    def test_json_formatter_includes_extra_fields(self):
        """extra_fields dict is merged into the top-level JSON object."""
        from agent_framework.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="mylogger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="structured event",
            args=(),
            exc_info=None,
        )
        record.extra_fields = {"request_id": "abc-123", "latency_ms": 42}
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed.get("request_id") == "abc-123"
        assert parsed.get("latency_ms") == 42

    def test_json_formatter_single_line(self):
        """Output is a single line (no embedded newlines)."""
        from agent_framework.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="mylogger",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="line one\nline two",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        # json.dumps produces a single line; the message may contain \n but
        # the JSON string escapes it — so the raw output should not have real newlines
        # except for json encoding of the \n character itself.
        assert "\n" not in output, "JSON output should be a single line"

    def test_json_formatter_timestamp_format(self):
        """Timestamp is in ISO-8601 UTC format ending with 'Z'."""
        from agent_framework.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="ts test",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        ts = parsed["timestamp"]
        assert ts.endswith("Z"), f"Timestamp should end with 'Z', got {ts!r}"
        # Should be parseable as a datetime
        from datetime import datetime
        # Strip 'Z' and parse
        datetime.fromisoformat(ts.rstrip("Z"))
