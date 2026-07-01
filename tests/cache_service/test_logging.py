"""
Tests for the LoggingMiddleware: verifies structured log output format and PII safety.
Uses the app_client fixture (stub lifespan, fake redis).
"""

from __future__ import annotations

import json
import sys
from io import StringIO

import pytest


def _imf_lookup_body(*, request_id=None, messages=None, pii_fields=None):
    body = {
        "request": {
            "messages": messages or [{"role": "user", "content": "hello test"}],
            "task_type": "chat",
        },
        "routing": {"selected_model": "llama3"},
        "governance": {"pii_fields_detected": pii_fields or []},
    }
    if request_id is not None:
        body["request_id"] = request_id
    return body


async def _capture_log(app_client, body, headers=None):
    """
    Post to /cache/lookup while capturing stdout and return the last JSON log entry
    that has a 'method' field (i.e. emitted by LoggingMiddleware).
    """
    buf = StringIO()
    orig = sys.stdout
    sys.stdout = buf
    try:
        resp = await app_client.post("/cache/lookup", json=body, headers=headers or {})
    finally:
        sys.stdout = orig

    lines = buf.getvalue().strip().splitlines()
    # The last line from the middleware is the per-request log entry
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if "method" in entry:
                return entry, resp
        except Exception:
            continue
    return None, resp


class TestLogEntryRequiredFields:
    async def test_log_entry_contains_required_fields(self, app_client):
        """Every request log entry has: timestamp, level, method, path, status_code, latency_ms, request_id."""
        body = _imf_lookup_body(request_id="req-123")
        entry, _ = await _capture_log(app_client, body)
        assert entry is not None, "No middleware log entry found"
        for field in ("timestamp", "level", "method", "path", "status_code", "latency_ms", "request_id"):
            assert field in entry, f"Missing field: {field}"


class TestRequestId:
    async def test_request_id_from_imf_body(self, app_client):
        """request_id is extracted from the IMF body field."""
        body = _imf_lookup_body(request_id="body-req-id-42")
        entry, _ = await _capture_log(app_client, body)
        assert entry is not None
        assert entry["request_id"] == "body-req-id-42"

    async def test_request_id_from_header_fallback(self, app_client):
        """Falls back to X-Request-ID header when body request_id is absent."""
        body = _imf_lookup_body()  # no request_id
        entry, _ = await _capture_log(
            app_client, body, headers={"X-Request-ID": "header-req-xyz"}
        )
        assert entry is not None
        assert entry["request_id"] == "header-req-xyz"

    async def test_request_id_unknown_fallback(self, app_client):
        """Falls back to 'unknown' when neither body nor header has request_id."""
        body = _imf_lookup_body()  # no request_id in body
        entry, _ = await _capture_log(app_client, body)
        assert entry is not None
        assert entry["request_id"] == "unknown"


class TestPiiSafety:
    async def test_pii_field_names_not_in_log(self, app_client):
        """
        Fields listed in governance.pii_fields_detected must not appear in the log entry.
        """
        body = _imf_lookup_body(pii_fields=["email", "phone"])
        entry, _ = await _capture_log(app_client, body)
        assert entry is not None
        assert "email" not in entry
        assert "phone" not in entry

    async def test_message_content_not_in_log(self, app_client):
        """
        Raw message content strings must not appear as log entry values.
        """
        secret_content = "my secret prompt content 99999"
        body = _imf_lookup_body(messages=[{"role": "user", "content": secret_content}])
        entry, _ = await _capture_log(app_client, body)
        assert entry is not None
        # The raw content string should not appear as any field value
        for val in entry.values():
            assert val != secret_content, f"PII content leaked into log: {val!r}"
