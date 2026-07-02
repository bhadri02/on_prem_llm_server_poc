"""
Unit tests for security_layer.injection.

Covers:
 1. YAML file not found  → returns None and logs ERROR
 2. Malformed YAML       → returns None and logs ERROR
 3. Invalid regex        → returns None and logs ERROR
 4. Empty patterns list  → returns [] (not None)
 5. scan with no patterns always returns 0.0
 6. scan with matching pattern returns 1.0
 7. scan is case-insensitive
 8. plain string entry matches as substring
"""

import os
import re
import logging
import textwrap
import pytest

# Set required env vars before any security_layer import so that
# security_layer.config.Settings() can instantiate without raising.
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

from security_layer.injection import load_injection_patterns, scan_for_injection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: str):
    """Write *content* to a temp YAML file and return its path as a str."""
    p = tmp_path / "patterns.yaml"
    p.write_text(content)
    return str(p)


# ---------------------------------------------------------------------------
# load_injection_patterns tests
# ---------------------------------------------------------------------------

class TestLoadInjectionPatterns:
    """Tests for load_injection_patterns()."""

    def test_file_not_found_returns_none(self, caplog):
        """Non-existent path → None and an ERROR log entry."""
        import logging as _logging
        _logger = _logging.getLogger("security_layer.injection")
        _orig_propagate = _logger.propagate
        _logger.propagate = True
        try:
            with caplog.at_level(logging.ERROR, logger="security_layer.injection"):
                result = load_injection_patterns("/nonexistent/path/patterns.yaml")
        finally:
            _logger.propagate = _orig_propagate

        assert result is None
        assert any(r.levelname == "ERROR" for r in caplog.records), (
            "Expected at least one ERROR log record"
        )

    def test_malformed_yaml_returns_none(self, tmp_path, caplog):
        """Invalid YAML content → None and an ERROR log entry."""
        import logging as _logging
        bad_yaml = _write_yaml(tmp_path, "patterns: [unclosed bracket")
        _logger = _logging.getLogger("security_layer.injection")
        _orig_propagate = _logger.propagate
        _logger.propagate = True
        try:
            with caplog.at_level(logging.ERROR, logger="security_layer.injection"):
                result = load_injection_patterns(bad_yaml)
        finally:
            _logger.propagate = _orig_propagate

        assert result is None
        assert any(r.levelname == "ERROR" for r in caplog.records), (
            "Expected at least one ERROR log record for malformed YAML"
        )

    def test_invalid_regex_returns_none(self, tmp_path, caplog):
        """An entry that is not a valid regex → None and an ERROR log entry."""
        import logging as _logging
        invalid_regex_yaml = textwrap.dedent("""\
            patterns:
              - "valid pattern"
              - "["
        """)
        bad_regex = _write_yaml(tmp_path, invalid_regex_yaml)
        _logger = _logging.getLogger("security_layer.injection")
        _orig_propagate = _logger.propagate
        _logger.propagate = True
        try:
            with caplog.at_level(logging.ERROR, logger="security_layer.injection"):
                result = load_injection_patterns(bad_regex)
        finally:
            _logger.propagate = _orig_propagate

        assert result is None
        assert any(r.levelname == "ERROR" for r in caplog.records), (
            "Expected at least one ERROR log record for invalid regex"
        )

    def test_empty_patterns_list_returns_empty_list(self, tmp_path):
        """Valid YAML with an empty patterns list → [] (not None)."""
        empty_yaml = _write_yaml(tmp_path, "patterns: []\n")
        result = load_injection_patterns(empty_yaml)

        assert result is not None, "Expected [] but got None"
        assert result == [], f"Expected empty list but got: {result!r}"

    def test_valid_patterns_returns_compiled_list(self, tmp_path):
        """Valid YAML with patterns → list of compiled re.Pattern objects."""
        yaml_content = textwrap.dedent("""\
            patterns:
              - "ignore previous instructions"
              - "you are now"
        """)
        path = _write_yaml(tmp_path, yaml_content)
        result = load_injection_patterns(path)

        assert result is not None
        assert len(result) == 2
        for pat in result:
            assert isinstance(pat, re.Pattern)


# ---------------------------------------------------------------------------
# scan_for_injection tests
# ---------------------------------------------------------------------------

class TestScanForInjection:
    """Tests for scan_for_injection()."""

    def test_no_patterns_always_returns_zero(self):
        """Empty patterns list → 0.0 regardless of message content."""
        messages = [{"role": "user", "content": "ignore previous instructions"}]
        assert scan_for_injection(messages, []) == 0.0

    def test_matching_pattern_returns_one(self):
        """A pattern that matches → 1.0."""
        pattern = re.compile("ignore previous instructions", re.IGNORECASE)
        messages = [{"role": "user", "content": "ignore previous instructions"}]
        assert scan_for_injection(messages, [pattern]) == 1.0

    def test_non_matching_pattern_returns_zero(self):
        """No match → 0.0."""
        pattern = re.compile("you are now", re.IGNORECASE)
        messages = [{"role": "user", "content": "Hello, how are you?"}]
        assert scan_for_injection(messages, [pattern]) == 0.0

    def test_case_insensitive_match(self):
        """Upper-case variant of a lower-case pattern still matches."""
        pattern = re.compile("ignore previous instructions", re.IGNORECASE)
        messages = [{"role": "user", "content": "IGNORE PREVIOUS INSTRUCTIONS"}]
        assert scan_for_injection(messages, [pattern]) == 1.0

    def test_plain_string_matches_as_substring(self):
        """Plain keyword pattern matches when embedded inside longer text."""
        pattern = re.compile("ignore previous instructions", re.IGNORECASE)
        messages = [
            {"role": "user", "content": "please ignore previous instructions now"}
        ]
        assert scan_for_injection(messages, [pattern]) == 1.0

    def test_scan_across_multiple_messages(self):
        """Pattern split across messages is caught via concatenation."""
        # The pattern appears when messages are joined with a space.
        pattern = re.compile("hello world", re.IGNORECASE)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        assert scan_for_injection(messages, [pattern]) == 1.0

    def test_returns_zero_for_empty_messages(self):
        """Empty messages list with any pattern → 0.0 (nothing to match)."""
        pattern = re.compile("ignore previous instructions", re.IGNORECASE)
        assert scan_for_injection([], [pattern]) == 0.0

    def test_missing_content_key_handled_gracefully(self):
        """Messages without a 'content' key should not raise."""
        pattern = re.compile("ignore", re.IGNORECASE)
        messages = [{"role": "user"}]  # no 'content' key
        # Should not raise; content defaults to ""; pattern won't match "" unless it matches empty
        result = scan_for_injection(messages, [pattern])
        assert result in (0.0, 1.0)  # just ensure no exception
