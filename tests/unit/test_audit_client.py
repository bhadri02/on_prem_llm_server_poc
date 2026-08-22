"""
tests/unit/test_audit_client.py — Unit tests for security_layer.audit_client.

Covers:
- HTTP 500 from Audit Store: does not raise, logs WARNING
- httpx.TimeoutException: does not raise, logs WARNING containing "timeout"
- httpx.ConnectError (connection refused): does not raise, logs WARNING
- X-API-Key header is present and correct in every POST request

post_audit_event() retries up to MAX_ATTEMPTS times with a short backoff
before giving up, so failure-path tests below register MAX_ATTEMPTS
responses/exceptions (pytest_httpx requires one registration per actual
outbound request) and patch asyncio.sleep to keep the tests fast.
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

import security_layer.audit_client as audit_client_module
from security_layer.audit_client import MAX_ATTEMPTS, flush_pending_audit_events, post_audit_event


@pytest.fixture(autouse=True)
def _reset_pending_queue():
    """_pending is process-global — clear it before/after each test so
    failures in one test's retry path can't leak into another test's
    assertions about queue state."""
    audit_client_module._pending.clear()
    yield
    audit_client_module._pending.clear()

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


async def _no_sleep(*_args, **_kwargs) -> None:
    """Replaces asyncio.sleep in retry-path tests so they don't take real
    wall-clock time waiting out the retry backoff."""
    return None


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
async def test_http_500_does_not_raise_and_logs_warning(httpx_mock, caplog, monkeypatch):
    """HTTP 500 from Audit Store must not raise and must emit a WARNING."""
    monkeypatch.setattr("security_layer.audit_client.asyncio.sleep", _no_sleep)
    for _ in range(MAX_ATTEMPTS):
        httpx_mock.add_response(status_code=500)

    with _attach_caplog(caplog):
        # Must not raise
        await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING log for HTTP 500 response"


@pytest.mark.asyncio
async def test_timeout_does_not_raise_and_logs_warning_with_timeout_keyword(
    httpx_mock, caplog, monkeypatch
):
    """TimeoutException must not raise and must log a WARNING containing 'timeout'."""
    monkeypatch.setattr("security_layer.audit_client.asyncio.sleep", _no_sleep)
    for _ in range(MAX_ATTEMPTS):
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
async def test_connection_refused_does_not_raise_and_logs_warning(httpx_mock, caplog, monkeypatch):
    """ConnectError (connection refused) must not raise and must emit a WARNING."""
    monkeypatch.setattr("security_layer.audit_client.asyncio.sleep", _no_sleep)
    for _ in range(MAX_ATTEMPTS):
        httpx_mock.add_exception(httpx.ConnectError("Connection refused"))

    with _attach_caplog(caplog):
        await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING log for connection refused"


# ---------------------------------------------------------------------------
# Pending queue / flush behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_queued_after_exhausting_retries(httpx_mock, monkeypatch):
    """After MAX_ATTEMPTS failures, the event lands in the pending queue."""
    monkeypatch.setattr("security_layer.audit_client.asyncio.sleep", _no_sleep)
    for _ in range(MAX_ATTEMPTS):
        httpx_mock.add_response(status_code=500)

    await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    assert len(audit_client_module._pending) == 1


@pytest.mark.asyncio
async def test_successful_attempt_does_not_queue(httpx_mock):
    """A 2xx response on the first attempt must not queue anything."""
    httpx_mock.add_response(status_code=200)

    await post_audit_event(SAMPLE_EVENT, AUDIT_URL, API_KEY)

    assert len(audit_client_module._pending) == 0


@pytest.mark.asyncio
async def test_flush_pending_resends_and_clears_on_success(httpx_mock):
    """flush_pending_audit_events must resend queued events and drop them
    from the queue once they succeed."""
    audit_client_module._pending.append((SAMPLE_EVENT, AUDIT_URL, API_KEY))
    httpx_mock.add_response(status_code=201)

    await flush_pending_audit_events()

    assert len(audit_client_module._pending) == 0
    requests = httpx_mock.get_requests()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_flush_pending_requeues_on_continued_failure(httpx_mock):
    """flush_pending_audit_events must put the event back if the retry also fails."""
    audit_client_module._pending.append((SAMPLE_EVENT, AUDIT_URL, API_KEY))
    httpx_mock.add_response(status_code=500)

    await flush_pending_audit_events()

    assert len(audit_client_module._pending) == 1


@pytest.mark.asyncio
async def test_flush_pending_noop_when_queue_empty():
    """flush_pending_audit_events must be a safe no-op on an empty queue."""
    await flush_pending_audit_events()  # must not raise
    assert len(audit_client_module._pending) == 0


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
