"""
tests/integration/test_startup.py — Integration tests for the Security &
Governance Layer lifespan startup validation.

Covers:
  - test_empty_downstream_router_url_exits  : DOWNSTREAM_ROUTER_URL="" → sys.exit(1)
  - test_empty_audit_store_url_exits        : AUDIT_STORE_URL="" → sys.exit(1)
  - test_empty_audit_api_key_exits          : AUDIT_API_KEY="" → sys.exit(1)
  - test_nonexistent_patterns_path_exits    : non-existent file → sys.exit(1)
  - test_malformed_yaml_patterns_exits      : malformed YAML → sys.exit(1)
  - test_empty_patterns_list_warns_but_starts: empty patterns → starts ok
  - test_valid_config_starts_successfully   : valid config → app.state fully set
"""

import os
import tempfile

import pytest
from fastapi import FastAPI
from unittest.mock import MagicMock, patch

from security_layer.main import lifespan


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mock_settings(
    downstream_router_url: str = "http://router:8082",
    audit_store_url: str = "http://audit:9200",
    audit_api_key: str = "test-key",
    injection_patterns_path: str = "/some/path",
    pii_enabled: bool = False,
) -> MagicMock:
    """Return a MagicMock that quacks like Settings."""
    s = MagicMock()
    s.downstream_router_url = downstream_router_url
    s.audit_store_url = audit_store_url
    s.audit_api_key = audit_api_key
    s.injection_patterns_path = injection_patterns_path
    s.pii_enabled = pii_enabled
    s.log_level = "WARNING"
    return s


# ---------------------------------------------------------------------------
# Failure cases — each must sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_downstream_router_url_exits():
    """DOWNSTREAM_ROUTER_URL="" triggers sys.exit(1)."""
    mock_settings = _mock_settings(downstream_router_url="")

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_empty_audit_store_url_exits():
    """AUDIT_STORE_URL="" triggers sys.exit(1)."""
    mock_settings = _mock_settings(audit_store_url="")

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_empty_audit_api_key_exits():
    """AUDIT_API_KEY="" triggers sys.exit(1)."""
    mock_settings = _mock_settings(audit_api_key="")

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_nonexistent_patterns_path_exits():
    """Non-existent INJECTION_PATTERNS_PATH triggers sys.exit(1)."""
    mock_settings = _mock_settings(
        injection_patterns_path="/tmp/does_not_exist_xyz_abc_999.yaml"
    )

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_malformed_yaml_patterns_exits(tmp_path):
    """Malformed YAML at INJECTION_PATTERNS_PATH triggers sys.exit(1)."""
    bad_yaml_file = tmp_path / "bad_patterns.yaml"
    # Write intentionally broken YAML (unmatched braces / invalid structure)
    bad_yaml_file.write_text("patterns: [\n  - 'unclosed bracket\n")

    mock_settings = _mock_settings(injection_patterns_path=str(bad_yaml_file))

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Warning path — starts successfully with empty patterns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_patterns_list_warns_but_starts(tmp_path):
    """Empty patterns list logs a WARNING but the lifespan completes normally."""
    patterns_file = tmp_path / "empty_patterns.yaml"
    patterns_file.write_text("patterns: []\n")

    mock_settings = _mock_settings(injection_patterns_path=str(patterns_file))

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        # Should NOT raise SystemExit
        async with lifespan(test_app):
            # App started — patterns list is empty but state is populated
            assert test_app.state.patterns == []
            assert test_app.state.analyzer is None   # pii_enabled=False
            assert test_app.state.anonymizer is None


# ---------------------------------------------------------------------------
# Happy path — valid configuration starts fully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_config_starts_successfully(tmp_path):
    """Valid configuration populates app.state.patterns and app.state.analyzer."""
    patterns_file = tmp_path / "patterns.yaml"
    patterns_file.write_text(
        "patterns:\n"
        "  - 'ignore previous instructions'\n"
        "  - 'you are now'\n"
    )

    mock_settings = _mock_settings(
        injection_patterns_path=str(patterns_file),
        pii_enabled=False,
    )

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        async with lifespan(test_app):
            assert test_app.state.patterns is not None
            assert len(test_app.state.patterns) == 2
            assert test_app.state.settings is mock_settings
            # PII disabled → analyzer/anonymizer remain None
            assert test_app.state.analyzer is None
            assert test_app.state.anonymizer is None
            # Blocklist is populated from content_safety.BLOCKLIST
            assert isinstance(test_app.state.blocklist, list)
            assert len(test_app.state.blocklist) > 0


@pytest.mark.asyncio
async def test_valid_config_pii_enabled_starts_successfully(tmp_path):
    """Valid config with pii_enabled=True initialises Presidio engines on app.state."""
    patterns_file = tmp_path / "patterns.yaml"
    patterns_file.write_text("patterns:\n  - 'ignore previous'\n")

    mock_settings = _mock_settings(
        injection_patterns_path=str(patterns_file),
        pii_enabled=True,
    )

    test_app = FastAPI(lifespan=lifespan)
    with patch("security_layer.main.settings", mock_settings):
        async with lifespan(test_app):
            assert test_app.state.analyzer is not None
            assert test_app.state.anonymizer is not None
