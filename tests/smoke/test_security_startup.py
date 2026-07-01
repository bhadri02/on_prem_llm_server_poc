"""
tests/smoke/test_security_startup.py — Startup smoke tests for the Security &
Governance Layer.

Subtasks covered:
  26.3 — create_app() with valid env vars and in-memory compiled patterns runs
          through lifespan successfully; app.state is fully populated.
  26.4 — With each of the four required env vars unset or empty, the lifespan
          raises SystemExit(1).
"""

import re
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from security_layer.main import lifespan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_settings(
    downstream_router_url: str = "http://router:8082",
    audit_store_url: str = "http://audit:9200",
    audit_api_key: str = "test-key",
    injection_patterns_path: str = "/placeholder",
    pii_enabled: bool = False,
) -> MagicMock:
    """Build a Settings-like MagicMock for smoke tests."""
    s = MagicMock()
    s.downstream_router_url = downstream_router_url
    s.audit_store_url = audit_store_url
    s.audit_api_key = audit_api_key
    s.injection_patterns_path = injection_patterns_path
    s.pii_enabled = pii_enabled
    s.log_level = "WARNING"
    return s


# ---------------------------------------------------------------------------
# Subtask 26.3 — startup smoke test with valid config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_valid_config_populates_app_state(tmp_path):
    """create_app() with valid env vars and in-memory patterns runs through lifespan.

    Validates (subtask 26.3):
      - app.state.patterns is a non-empty list of compiled regex objects
      - app.state.analyzer is None when PII_ENABLED=false (no Presidio overhead)
      - app.state.settings.downstream_router_url is set and non-empty
      - app.state.blocklist is a non-empty list (from content_safety.BLOCKLIST)
    """
    # Write a small but valid patterns file
    patterns_file = tmp_path / "smoke_patterns.yaml"
    patterns_file.write_text(
        "patterns:\n"
        "  - 'ignore previous instructions'\n"
        "  - 'you are now'\n"
        "  - 'pretend you are'\n"
        "  - 'disregard your'\n"
    )

    mock_settings = _mock_settings(
        injection_patterns_path=str(patterns_file),
        pii_enabled=False,
    )

    test_app = FastAPI(lifespan=lifespan)

    with patch("security_layer.main.settings", mock_settings):
        async with lifespan(test_app):
            # --- app.state.patterns: non-empty list of compiled regex objects ---
            assert test_app.state.patterns is not None, \
                "app.state.patterns must be set after startup"
            assert isinstance(test_app.state.patterns, list), \
                "app.state.patterns must be a list"
            assert len(test_app.state.patterns) > 0, \
                "app.state.patterns must be non-empty"
            for pat in test_app.state.patterns:
                assert isinstance(pat, re.Pattern), (
                    f"Each entry in app.state.patterns must be a compiled re.Pattern; "
                    f"got {type(pat)!r}"
                )

            # --- app.state.analyzer: None when pii_enabled=False ---
            assert test_app.state.analyzer is None, \
                "app.state.analyzer must be None when PII_ENABLED=false"

            # --- app.state.settings.downstream_router_url is set ---
            assert test_app.state.settings is mock_settings, \
                "app.state.settings must be the Settings object"
            assert test_app.state.settings.downstream_router_url, \
                "app.state.settings.downstream_router_url must be non-empty"

            # --- app.state.blocklist populated ---
            assert isinstance(test_app.state.blocklist, list), \
                "app.state.blocklist must be a list"
            assert len(test_app.state.blocklist) > 0, \
                "app.state.blocklist must be non-empty (loaded from content_safety.BLOCKLIST)"


@pytest.mark.asyncio
async def test_startup_pii_enabled_initialises_presidio(tmp_path):
    """When PII_ENABLED=true, app.state.analyzer must be non-None after startup.

    Validates the PII_ENABLED=true branch of subtask 26.3.
    """
    patterns_file = tmp_path / "patterns.yaml"
    patterns_file.write_text("patterns:\n  - 'ignore previous instructions'\n")

    mock_settings = _mock_settings(
        injection_patterns_path=str(patterns_file),
        pii_enabled=True,
    )

    test_app = FastAPI(lifespan=lifespan)

    with patch("security_layer.main.settings", mock_settings):
        async with lifespan(test_app):
            # app.state.analyzer is not None when PII is enabled
            assert test_app.state.analyzer is not None, (
                "app.state.analyzer must be set (non-None) when PII_ENABLED=true"
            )
            assert test_app.state.anonymizer is not None, (
                "app.state.anonymizer must be set (non-None) when PII_ENABLED=true"
            )


# ---------------------------------------------------------------------------
# Subtask 26.4 — startup refusal when required env vars are missing/empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field,empty_value",
    [
        ("downstream_router_url", ""),
        ("audit_store_url", ""),
        ("audit_api_key", ""),
        ("injection_patterns_path", ""),
    ],
    ids=[
        "DOWNSTREAM_ROUTER_URL_empty",
        "AUDIT_STORE_URL_empty",
        "AUDIT_API_KEY_empty",
        "INJECTION_PATTERNS_PATH_empty",
    ],
)
async def test_startup_refuses_on_empty_required_field(missing_field, empty_value):
    """With each required env var empty, lifespan raises SystemExit(1) (subtask 26.4).

    Tests all four required fields:
      - DOWNSTREAM_ROUTER_URL
      - AUDIT_STORE_URL
      - AUDIT_API_KEY
      - INJECTION_PATTERNS_PATH
    """
    # Build settings with the targeted field set to empty string
    kwargs = {
        "downstream_router_url": "http://router:8082",
        "audit_store_url": "http://audit:9200",
        "audit_api_key": "test-key",
        "injection_patterns_path": "/placeholder/path.yaml",
    }
    kwargs[missing_field] = empty_value
    mock_settings = _mock_settings(**kwargs)

    test_app = FastAPI(lifespan=lifespan)

    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover — lifespan must exit before yielding

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) when {missing_field}='' "
        f"but got exit code {exc_info.value.code}"
    )


@pytest.mark.asyncio
async def test_startup_refuses_on_nonexistent_patterns_path():
    """INJECTION_PATTERNS_PATH pointing to a non-existent file → SystemExit(1).

    This tests a populated but invalid path (the file does not exist), which
    triggers load_injection_patterns returning None → sys.exit(1) in the lifespan.
    """
    mock_settings = _mock_settings(
        injection_patterns_path="/tmp/does_not_exist_smoke_test_xyz_999.yaml"
    )

    test_app = FastAPI(lifespan=lifespan)

    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1
