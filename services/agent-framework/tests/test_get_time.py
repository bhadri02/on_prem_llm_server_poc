# Feature: agent-framework — Unit tests for get_current_time tool
"""
tests/test_get_time.py

Unit tests for the get_current_time tool covering Requirements 7.2, 7.3, 7.4.

Requirements covered:
  7.2 — Return value is a non-empty string
  7.3 — Return value is a valid ISO-8601 datetime string (parseable by datetime.fromisoformat)
  7.4 — Return value contains '+00:00' UTC offset
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from tools.get_time import get_current_time


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def invoke() -> str:
    """Call the LangChain @tool via its .invoke() interface."""
    return get_current_time.invoke({})


# ---------------------------------------------------------------------------
# 1. Return value is a non-empty string — Requirement 7.2
# ---------------------------------------------------------------------------


class TestGetCurrentTimeNonEmpty:
    """Validates Requirement 7.2 — return value is a non-empty string."""

    def test_returns_string(self):
        """get_current_time() must return a str."""
        result = invoke()
        assert isinstance(result, str)

    def test_returns_non_empty_string(self):
        """get_current_time() must return a non-empty string."""
        result = invoke()
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 2. Return value is valid ISO-8601 — Requirement 7.3
# ---------------------------------------------------------------------------


class TestGetCurrentTimeIso8601:
    """Validates Requirement 7.3 — result is parseable by datetime.fromisoformat."""

    def test_parses_as_iso8601(self):
        """datetime.fromisoformat(result) must not raise."""
        result = invoke()
        # Raises ValueError if not valid ISO-8601 — that would fail the test
        parsed = datetime.fromisoformat(result)
        assert isinstance(parsed, datetime)


# ---------------------------------------------------------------------------
# 3. Return value contains '+00:00' UTC offset — Requirement 7.4
# ---------------------------------------------------------------------------


class TestGetCurrentTimeUtcOffset:
    """Validates Requirement 7.4 — result contains the '+00:00' UTC offset."""

    def test_contains_utc_offset(self):
        """Result must contain '+00:00' to indicate UTC timezone."""
        result = invoke()
        assert "+00:00" in result, (
            f"Expected '+00:00' in result but got: {result!r}"
        )


# ---------------------------------------------------------------------------
# 4. Exception handling — error string returned when system clock unavailable
# ---------------------------------------------------------------------------


class TestGetCurrentTimeErrorHandling:
    """Bonus: verifies the try/except guard returns an error string when
    datetime.now raises an exception (simulates an unavailable system clock)."""

    def test_system_clock_exception_returns_error_string(self):
        """When datetime.now raises, get_current_time returns an 'Error:' string."""
        with patch(
            "tools.get_time.datetime",
        ) as mock_dt:
            mock_dt.now.side_effect = OSError("clock unavailable")
            result = get_current_time.invoke({})

        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "clock" in result.lower() or "system" in result.lower() or "time" in result.lower()
