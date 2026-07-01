"""
tests/unit/test_audit_client.py — Unit tests for security_layer.audit_client.

Covers:
- HTTP 500 from Audit Store: does not raise, logs WARNING
- httpx.TimeoutException: does not raise, logs WARNING containing "timeout"
- httpx.ConnectError (connection refused): does not raise, logs WARNING
- X-API-Key header is present and correct in every POST request
"""

import logging
import os

# Set required env vars before any security_layer import so that
# security_layer.config.Settings() can instantiate without raising.
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit-store:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

import httpx
import pytest

from security_layer.audit_client import post_audit_event

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_EVENT = {
    "request_id": "12345678-1234-4234-8234-123456789abc",
    "layer": "security",
    "event_type": "request_received",
    "outcome": "pass",
}
AUDIT_URL = "http://audit-store:9200"
API_KEY = "test-api-key"

# The audit_client logger uses get_logger(__name__) with propagate=False.
# We must attach the caplog handler directly to that logger so pytest can
# capture its output.
LOGGER_NAME = "security_layer.audit_client"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _attach_caplog(caplog, level: int = logging.WARNING):
    """Attach the pytest caplog handler to the non-propagating module logger.

    Returns a context manager that cleans up afterward.
    """
    logger = logging.getLogger(LOGGER_NAME)

    class _Ctx:
        def __enter__(self):
            caplog.set_level(level, logger=LOGGER_NAME)
            # caplog only captures records that reach the root logger via
            # propagation. Since our logger has propagate=False we must add
            # the caplog handler directly.
            logger.addHandler(caplog.handler)
            return self

        def __exit__(self, *_):
            logger.removeHandler(caplog.handler)

    return _Ctx()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_500_does_not_raise_and_logs_warning(httpx_mock, caplog):
    """HTTP 500 from Audit Store must not raise and must emit a WARNING."""
    httpx_mock.add_response(status_code=500)

    with _attach_caplog(caplog):
        # Must not raise
        await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING log for HTTP 500 response"


@pytest.mark.asyncio
async def test_timeout_does_not_raise_and_logs_warning_with_timeout_keyword(
    httpx_mock, caplog
):
    """TimeoutException must not raise and must log a WARNING containing 'timeout'."""
    httpx_mock.add_exception(httpx.TimeoutException("timeout"))

    with _attach_caplog(caplog):
        await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING log for timeout"

    # The word "timeout" must appear in the log message or extra fields
    found_timeout_keyword = False
    for record in warnings:
        msg_lower = record.getMessage().lower()
        extra_fields = getattr(record, "extra_fields", {}) or {}
        extra_str = str(extra_fields).lower()
        if "timeout" in msg_lower or "timeout" in extra_str:
            found_timeout_keyword = True
            break

    assert found_timeout_keyword, (
        "Expected the word 'timeout' in the WARNING message or extra fields"
    )


@pytest.mark.asyncio
async def test_connection_refused_does_not_raise_and_logs_warning(httpx_mock, caplog):
    """ConnectError (connection refused) must not raise and must emit a WARNING."""
    httpx_mock.add_exception(httpx.ConnectError("Connection refused"))

    with _attach_caplog(caplog):
        await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING log for connection refused"


@pytest.mark.asyncio
async def test_x_api_key_header_included_in_every_post(httpx_mock):
    """X-API-Key header must equal the provided api_key value in every POST."""
    httpx_mock.add_response(status_code=200)

    await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    requests = httpx_mock.get_requests()
    assert requests, "Expected at least one HTTP request to be made"

    for request in requests:
        assert request.headers.get("X-API-Key") == API_KEY, (
            f"Expected X-API-Key header to be '{API_KEY}', "
            f"got '{request.headers.get('X-API-Key')}'"
        )
