"""
tests/smoke/test_startup.py -- Startup validation smoke tests for the API Gateway.

Covers:
  9.2.5 -- Startup with empty GATEWAY_API_KEY raises pydantic.ValidationError
            (the @field_validator raises ValueError, pydantic wraps it)
  9.2.6 -- LOG_LEVEL=ERROR suppresses INFO log entries emitted by
            LoggingMiddleware for 2xx/3xx/4xx responses

``api_gateway.main`` calls ``get_settings()`` at module import time and
calls ``sys.exit(1)`` if ``GATEWAY_API_KEY`` is missing.  All imports from
``api_gateway.main`` are deferred to inside test functions (after monkeypatch
has set the required env vars) to avoid that guard during pytest collection.

For the startup-validation test we bypass main.py entirely and instantiate
``Settings`` directly -- this is the recommended approach described in the
spec's architecture notes.

Validates: Requirements 2.1, 8.3
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

VALID_KEY = "test-key"
DOWNSTREAM_URL = "http://security-layer:8081"


# ---------------------------------------------------------------------------
# Identity resolution stub (Phase 2 — RBAC + per-user API keys)
#
# AuthMiddleware now resolves X-Api-Key against the Admin Portal instead of
# a static comparison. These tests aren't exercising identity resolution, so
# mirror the old static-key behaviour: VALID_KEY resolves to a developer
# identity, anything else is unresolved.
# ---------------------------------------------------------------------------

async def _fake_resolve_key(key, client):
    from api_gateway.services.key_resolver import KeyProfile

    if key == VALID_KEY:
        return KeyProfile(
            user_id="poc-user",
            username="poc-user",
            department="poc",
            roles=["developer"],
            model_entitlements=[],
            key_id="test-key-id",
            rate_limit_override=60,
        )
    return None


# ---------------------------------------------------------------------------
# 9.2.5 -- Empty GATEWAY_API_KEY raises pydantic.ValidationError
# ---------------------------------------------------------------------------


def test_startup_empty_gateway_api_key_raises(monkeypatch):
    """Settings() with GATEWAY_API_KEY='' must raise pydantic.ValidationError.

    The @field_validator raises ValueError for empty/missing keys; pydantic
    wraps that in ValidationError before it surfaces to callers.

    We instantiate Settings() directly (not via create_app / main.py) to
    avoid the sys.exit(1) side-effect in the module-level guard.

    Validates: Requirements 2.1
    """
    monkeypatch.setenv("GATEWAY_API_KEY", "")
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)

    from api_gateway.config import Settings, get_settings

    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    error_str = str(exc_info.value)
    assert "GATEWAY_API_KEY" in error_str, (
        f"Expected 'GATEWAY_API_KEY' to appear in ValidationError message:\n{error_str}"
    )

    get_settings.cache_clear()


def test_startup_whitespace_only_gateway_api_key_raises(monkeypatch):
    """Settings() with GATEWAY_API_KEY='   ' (whitespace only) must also raise.

    The validator strips and treats blank-only values as empty.

    Validates: Requirements 2.1
    """
    monkeypatch.setenv("GATEWAY_API_KEY", "   ")
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)

    from api_gateway.config import Settings, get_settings

    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        Settings()

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 9.2.6 -- LOG_LEVEL=ERROR suppresses INFO log entries
# ---------------------------------------------------------------------------


def _is_logging_middleware_entry(record: dict) -> bool:
    """Return True if *record* is a LoggingMiddleware request log entry.

    LoggingMiddleware entries use "timestamp" (not "timestamp_utc") and
    include method, path, status_code, latency_ms with no "level" key for
    normal (non-error) responses.

    Audit events are distinguished by "audit_id" / "timestamp_utc" and must
    not be confused with LoggingMiddleware entries.
    """
    return (
        "timestamp" in record           # LoggingMiddleware key
        and "timestamp_utc" not in record   # audit event key -- absent in log entries
        and "audit_id" not in record        # audit event key -- absent in log entries
        and "status_code" in record
        and "level" not in record
    )


def test_log_level_error_suppresses_info_entries(monkeypatch, capsys):
    """With LOG_LEVEL=ERROR, LoggingMiddleware must NOT emit INFO request log entries.

    LoggingMiddleware emits a JSON line at INFO level for 2xx/3xx/4xx responses.
    When LOG_LEVEL=ERROR, _should_emit(INFO) returns False and the line is
    skipped entirely.

    Strategy:
      1. Set LOG_LEVEL=ERROR and make a valid GET /v1/models request (200).
      2. Capture stdout with capsys.
      3. For every JSON line in stdout, assert it is NOT a LoggingMiddleware
         request log entry (identified by "timestamp" key, no "timestamp_utc",
         no "audit_id", "status_code" present, no "level" key).

    Validates: Requirements 8.3
    """
    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    import fakeredis.aioredis

    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from starlette.testclient import TestClient

    get_settings.cache_clear()
    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", _fake_resolve_key)

    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        resp = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})

    assert resp.status_code == 200, (
        f"Pre-condition failed: GET /v1/models returned {resp.status_code}"
    )

    captured = capsys.readouterr()
    stdout_lines = [ln.strip() for ln in captured.out.splitlines() if ln.strip()]

    for line in stdout_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # non-JSON lines are not our concern

        assert not _is_logging_middleware_entry(record), (
            "Found an INFO-level LoggingMiddleware entry in stdout despite LOG_LEVEL=ERROR:\n"
            f"  {line}\n"
            "LoggingMiddleware should suppress INFO entries when LOG_LEVEL=ERROR."
        )

    get_settings.cache_clear()


def test_log_level_info_emits_request_log_entries(monkeypatch, capsys):
    """With LOG_LEVEL=INFO (default), LoggingMiddleware DOES emit request log entries.

    This is the positive counterpart to test_log_level_error_suppresses_info_entries:
    it confirms that the suppression test is meaningful by verifying entries ARE
    emitted when the log level permits them.

    Validates: Requirements 8.1, 8.3
    """
    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    import fakeredis.aioredis

    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from starlette.testclient import TestClient

    get_settings.cache_clear()
    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", _fake_resolve_key)

    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        resp = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})

    assert resp.status_code == 200, (
        f"Pre-condition failed: GET /v1/models returned {resp.status_code}"
    )

    captured = capsys.readouterr()
    stdout_lines = [ln.strip() for ln in captured.out.splitlines() if ln.strip()]

    # At least one line should be a LoggingMiddleware request log entry.
    log_entries = [
        json.loads(ln)
        for ln in stdout_lines
        if ln.startswith("{") and _is_logging_middleware_entry(json.loads(ln))
    ]

    assert len(log_entries) >= 1, (
        "Expected at least one INFO request log entry in stdout with LOG_LEVEL=INFO, "
        "but found none.\nAll stdout lines:\n" + "\n".join(stdout_lines)
    )

    # Spot-check required fields on the first entry.
    entry = log_entries[0]
    for field in ("request_id", "timestamp", "method", "path", "status_code", "latency_ms"):
        assert field in entry, (
            f"Expected field '{field}' in request log entry, got keys: {list(entry.keys())}"
        )

    get_settings.cache_clear()
