"""
Example tests for the structured JSON logging middleware.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""

import json

import pytest

# Fixed API key matching the test fixtures in conftest.py
TEST_KEY = "test-secret-key"


def _parse_request_log_line(captured_out: str) -> dict:
    """
    Parse the last JSON-formatted log line from captured stdout.

    The lifespan may emit a startup warning if the API key is unset, but
    in tests the key IS set so we expect only request log lines. We parse
    the last non-empty line as the request entry.
    """
    lines = [ln.strip() for ln in captured_out.strip().split("\n") if ln.strip()]
    assert lines, "No output was captured — expected at least one log line"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# One JSON line per request
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_each_request_emits_one_json_line(async_client, capsys):
    """Each HTTP request emits exactly one JSON log line to stdout."""
    capsys.readouterr()  # clear any startup output

    await async_client.get("/health")

    captured = capsys.readouterr()
    lines = [ln.strip() for ln in captured.out.strip().split("\n") if ln.strip()]
    assert len(lines) == 1, f"Expected 1 log line, got {len(lines)}: {lines}"


# ---------------------------------------------------------------------------
# Required fields present
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_entry_has_required_fields(async_client, capsys):
    """Each log entry contains timestamp, level, method, path, status_code, latency_ms."""
    capsys.readouterr()

    await async_client.get("/health")

    captured = capsys.readouterr()
    entry = _parse_request_log_line(captured.out)

    assert "timestamp" in entry
    assert "level" in entry
    assert "method" in entry
    assert "path" in entry
    assert "status_code" in entry
    assert "latency_ms" in entry


# ---------------------------------------------------------------------------
# Correct field values
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_entry_method_and_path_correct(async_client, capsys):
    """Log entry reflects the actual HTTP method and path."""
    capsys.readouterr()

    await async_client.get("/health")

    captured = capsys.readouterr()
    entry = _parse_request_log_line(captured.out)

    assert entry["method"] == "GET"
    assert entry["path"] == "/health"
    assert entry["status_code"] == 200


# ---------------------------------------------------------------------------
# Level: INFO for 2xx
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_level_info_for_2xx(async_client, capsys):
    """Log entry level is 'INFO' for 2xx responses."""
    capsys.readouterr()

    await async_client.get("/health")

    captured = capsys.readouterr()
    entry = _parse_request_log_line(captured.out)

    assert entry["level"] == "INFO"


# ---------------------------------------------------------------------------
# Level: ERROR for 5xx
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_level_error_for_5xx(async_client, capsys):
    """Log entry level is 'ERROR' for 5xx responses."""
    from unittest.mock import patch

    from model_registry.exceptions import PersistenceError
    from model_registry.main import app

    capsys.readouterr()

    # Force get_all() to raise a PersistenceError, which the app exception
    # handler translates to HTTP 500 without propagating through the middleware.
    with patch.object(
        app.state.storage,
        "get_all",
        side_effect=PersistenceError("injected 500"),
    ):
        response = await async_client.get("/models/")

    assert response.status_code == 500

    captured = capsys.readouterr()
    entry = _parse_request_log_line(captured.out)

    assert entry["level"] == "ERROR"
    assert entry["status_code"] == 500


# ---------------------------------------------------------------------------
# X-API-Key value not logged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_key_value_not_in_log(async_client, capsys):
    """The X-API-Key header value must NOT appear in the emitted log line."""
    capsys.readouterr()

    # POST to /models (no trailing slash — auth middleware path match)
    await async_client.post(
        "/models",
        json={
            "name": "key-test-model",
            "version": "1.0",
            "backend": "ollama",
            "endpoint": "http://inference:11434",
            "tasks": ["chat"],
            "status": "active",
        },
        headers={"X-API-Key": TEST_KEY},
        follow_redirects=True,
    )

    captured = capsys.readouterr()
    # The API key value must not appear anywhere in the stdout output
    assert TEST_KEY not in captured.out, (
        f"API key value leaked into log output: {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# latency_ms is a number
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_latency_ms_is_numeric(async_client, capsys):
    """latency_ms in the log entry is a non-negative float."""
    capsys.readouterr()

    await async_client.get("/health")

    captured = capsys.readouterr()
    entry = _parse_request_log_line(captured.out)

    assert isinstance(entry["latency_ms"], (int, float))
    assert entry["latency_ms"] >= 0
