"""
tests/unit/test_router_audit_client.py

Unit tests for intelligent_router.audit_client.post_audit_event.

The function signature is:
    post_audit_event(event: dict, audit_store_url: str,
                     http_client: httpx.AsyncClient) -> None

Covers (per task 11.1):
  - HTTP 500 from Audit Store → logs WARNING, does not raise
  - httpx.TimeoutException   → logs WARNING with "timeout" in message, does not raise
  - connection refused (ConnectError) → logs WARNING, does not raise
  - HTTP 201 (success)       → no WARNING logged

pytest-httpx (httpx_mock) intercepts outbound calls.
asyncio_mode = auto (pytest.ini) — no @pytest.mark.asyncio needed.

NOTE on log capture: the audit_client logger uses get_logger(__name__) which
attaches a StreamHandler(stdout) and sets propagate=False.  caplog only
captures records that propagate to the root logger.  To work around this we
patch the module-level logger directly with unittest.mock.patch — the same
pattern used in tests/unit/test_cache_client.py.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

import os

# Ensure the three required env vars are present before any intelligent_router
# import triggers pydantic-settings validation.
for _k, _v in [
    ("MODEL_MATRIX_PATH", "/tmp/model_matrix.yaml"),
    ("TASK_RULES_PATH", "/tmp/task_rules.yaml"),
    ("AUDIT_STORE_URL", "http://audit-store:9200"),
]:
    os.environ.setdefault(_k, _v)

from unittest.mock import patch

import httpx
import pytest

from intelligent_router.audit_client import post_audit_event

# ---------------------------------------------------------------------------
# Constants shared across all tests
# ---------------------------------------------------------------------------

AUDIT_STORE_URL = "http://audit-store:9200"
AUDIT_EVENTS_URL = f"{AUDIT_STORE_URL}/audit/events"

SAMPLE_EVENT = {
    "request_id": "00000000-0000-4000-8000-000000000099",
    "layer": "router",
    "event_type": "inference_complete",
    "outcome": "pass",
}


# ---------------------------------------------------------------------------
# HTTP 500 → logs WARNING and does not raise
# ---------------------------------------------------------------------------


class TestAuditWriteHttp500:
    async def test_does_not_raise_on_500(self, httpx_mock):
        httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=500)

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                # Must not raise
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_called_once()

    async def test_logs_warning_message_on_500(self, httpx_mock):
        httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=500)

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "audit_write_non_2xx"

    async def test_non_2xx_applies_to_all_3xx_and_above(self, httpx_mock):
        """Status codes 300+ (301, 404, 503) all trigger the WARNING branch."""
        for status_code in (301, 400, 404, 503):
            httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=status_code)

            with patch("intelligent_router.audit_client.logger") as mock_logger:
                async with httpx.AsyncClient() as client:
                    await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout → logs WARNING with "timeout" keyword and does not raise
# ---------------------------------------------------------------------------


class TestAuditWriteTimeout:
    async def test_does_not_raise_on_connect_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out", request=None),
            url=AUDIT_EVENTS_URL,
        )

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                # Must not raise
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_called_once()

    async def test_does_not_raise_on_read_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ReadTimeout("read timed out", request=None),
            url=AUDIT_EVENTS_URL,
        )

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_called_once()

    async def test_timeout_warning_message_contains_timeout_keyword(self, httpx_mock):
        """The WARNING log message for a timeout must contain the string 'timeout'."""
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out", request=None),
            url=AUDIT_EVENTS_URL,
        )

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        call_args = mock_logger.warning.call_args
        # The first positional argument is the log message string
        log_message: str = call_args[0][0]
        assert "timeout" in log_message.lower(), (
            f"Expected 'timeout' in WARNING message, got: {log_message!r}"
        )


# ---------------------------------------------------------------------------
# Connection refused (ConnectError) → logs WARNING and does not raise
# ---------------------------------------------------------------------------


class TestAuditWriteConnectionRefused:
    async def test_does_not_raise_on_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=AUDIT_EVENTS_URL,
        )

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                # Must not raise
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_called_once()

    async def test_logs_audit_write_failed_on_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=AUDIT_EVENTS_URL,
        )

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "audit_write_failed"

    async def test_never_raises_regardless_of_exception_type(self, httpx_mock):
        """post_audit_event must never propagate any exception to the caller."""
        httpx_mock.add_exception(
            httpx.ConnectError("unreachable"),
            url=AUDIT_EVENTS_URL,
        )

        async with httpx.AsyncClient() as client:
            # Should not raise — no assertion needed beyond reaching this line
            await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)


# ---------------------------------------------------------------------------
# HTTP 201 (success) → no WARNING logged
# ---------------------------------------------------------------------------


class TestAuditWriteSuccess:
    async def test_no_warning_on_201(self, httpx_mock):
        """A successful 201 response must produce zero WARNING log calls."""
        httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=201)

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_not_called()

    async def test_no_warning_on_200(self, httpx_mock):
        """200 OK also produces zero WARNING log calls."""
        httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=200)

        with patch("intelligent_router.audit_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        mock_logger.warning.assert_not_called()

    async def test_posts_to_correct_url(self, httpx_mock):
        """Verifies the POST is sent to {audit_store_url}/audit/events."""
        httpx_mock.add_response(url=AUDIT_EVENTS_URL, status_code=201)

        async with httpx.AsyncClient() as client:
            await post_audit_event(SAMPLE_EVENT, AUDIT_STORE_URL, client)

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert str(requests[0].url) == AUDIT_EVENTS_URL
        assert requests[0].method == "POST"
