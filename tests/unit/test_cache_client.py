"""
tests/unit/test_cache_client.py

Unit tests for intelligent_router.cache_client.

Covers:
  cache_lookup
    - HTTP 200 → returns the parsed response dict
    - non-200 (e.g. 503) → returns {"hit": False} and logs WARNING
    - httpx.TimeoutException → returns {"hit": False} and logs WARNING
    - httpx.ConnectError (general exception) → returns {"hit": False} and logs WARNING

  cache_write
    - timeout → logs WARNING and does not raise
    - non-200 (e.g. 503) → logs WARNING and does not raise
    - connection error → logs WARNING and does not raise
    - HTTP 200 → completes silently (no WARNING logged)

pytest-httpx (httpx_mock fixture) intercepts all outbound httpx calls.
asyncio_mode = auto (pytest.ini) — no @pytest.mark.asyncio needed.

NOTE on log capture: the cache_client logger uses get_logger() which attaches
a StreamHandler(stdout) and disables propagation. caplog only intercepts
records that reach the root logger, so we patch logger.warning directly
with unittest.mock.patch to verify warnings are emitted.
"""

import logging
from unittest.mock import patch, MagicMock

import httpx
import pytest

from intelligent_router.cache_client import cache_lookup, cache_write

CACHE_URL = "http://cache:8086"
LOOKUP_URL = f"{CACHE_URL}/cache/lookup"
WRITE_URL = f"{CACHE_URL}/cache/write"

MESSAGES = [{"role": "user", "content": "Hello"}]
MODEL = "llama3.2-3b"
TASK_TYPE = "chat"
REQUEST_ID = "00000000-0000-4000-8000-000000000001"
RESPONSE_IMF = {
    "request_id": REQUEST_ID,
    "response": {"content": "Hi there", "finish_reason": "stop"},
}


# ---------------------------------------------------------------------------
# cache_lookup — HTTP 200 returns parsed dict
# ---------------------------------------------------------------------------


class TestCacheLookupHttp200:
    async def test_returns_parsed_dict_on_200(self, httpx_mock):
        expected = {"hit": True, "cache_key": "abc123", "response": {"content": "cached"}}
        httpx_mock.add_response(url=LOOKUP_URL, status_code=200, json=expected)

        async with httpx.AsyncClient() as client:
            result = await cache_lookup(MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client)

        assert result == expected

    async def test_200_with_hit_false_body_returned_as_is(self, httpx_mock):
        """Even if the cache returns hit=False with a 200, we pass the dict through."""
        expected = {"hit": False}
        httpx_mock.add_response(url=LOOKUP_URL, status_code=200, json=expected)

        async with httpx.AsyncClient() as client:
            result = await cache_lookup(MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client)

        assert result == {"hit": False}


# ---------------------------------------------------------------------------
# cache_lookup — non-200 returns {"hit": False} and logs WARNING
# ---------------------------------------------------------------------------


class TestCacheLookupNon200:
    async def test_returns_hit_false_on_503(self, httpx_mock):
        httpx_mock.add_response(url=LOOKUP_URL, status_code=503)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                result = await cache_lookup(
                    MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client
                )

        assert result == {"hit": False}
        mock_logger.warning.assert_called_once()

    async def test_logs_request_id_on_non_200(self, httpx_mock):
        httpx_mock.add_response(url=LOOKUP_URL, status_code=404)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_lookup(MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_lookup_non_200"


# ---------------------------------------------------------------------------
# cache_lookup — timeout returns {"hit": False} and logs WARNING
# ---------------------------------------------------------------------------


class TestCacheLookupTimeout:
    async def test_returns_hit_false_on_connect_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out", request=None),
            url=LOOKUP_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                result = await cache_lookup(
                    MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client
                )

        assert result == {"hit": False}
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_lookup_timeout"

    async def test_returns_hit_false_on_read_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ReadTimeout("read timed out", request=None),
            url=LOOKUP_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                result = await cache_lookup(
                    MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client
                )

        assert result == {"hit": False}
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# cache_lookup — general exception (ConnectError) returns {"hit": False} and logs WARNING
# ---------------------------------------------------------------------------


class TestCacheLookupConnectError:
    async def test_returns_hit_false_on_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=LOOKUP_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                result = await cache_lookup(
                    MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client
                )

        assert result == {"hit": False}
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_lookup_failed"

    async def test_never_raises_on_any_exception(self, httpx_mock):
        """cache_lookup must never propagate exceptions to the caller."""
        httpx_mock.add_exception(
            httpx.ConnectError("unreachable"),
            url=LOOKUP_URL,
        )

        async with httpx.AsyncClient() as client:
            # Should not raise
            result = await cache_lookup(
                MESSAGES, MODEL, TASK_TYPE, REQUEST_ID, CACHE_URL, client
            )

        assert result == {"hit": False}


# ---------------------------------------------------------------------------
# cache_write — timeout logs WARNING and does not raise
# ---------------------------------------------------------------------------


class TestCacheWriteTimeout:
    async def test_does_not_raise_on_connect_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out", request=None),
            url=WRITE_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                # Must not raise
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_write_timeout"

    async def test_does_not_raise_on_read_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ReadTimeout("read timed out", request=None),
            url=WRITE_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# cache_write — non-200 logs WARNING and does not raise
# ---------------------------------------------------------------------------


class TestCacheWriteNon200:
    async def test_does_not_raise_on_503(self, httpx_mock):
        httpx_mock.add_response(url=WRITE_URL, status_code=503)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_write_non_200"

    async def test_does_not_raise_on_400(self, httpx_mock):
        httpx_mock.add_response(url=WRITE_URL, status_code=400)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()

    async def test_does_not_raise_on_301(self, httpx_mock):
        """Status >= 300 triggers the warning branch."""
        httpx_mock.add_response(url=WRITE_URL, status_code=301)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# cache_write — connection error logs WARNING and does not raise
# ---------------------------------------------------------------------------


class TestCacheWriteConnectError:
    async def test_does_not_raise_on_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=WRITE_URL,
        )

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "cache_write_failed"


# ---------------------------------------------------------------------------
# cache_write — HTTP 200 completes silently (no WARNING)
# ---------------------------------------------------------------------------


class TestCacheWriteSuccess:
    async def test_no_warning_on_200(self, httpx_mock):
        httpx_mock.add_response(url=WRITE_URL, status_code=200)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_not_called()

    async def test_no_warning_on_201(self, httpx_mock):
        httpx_mock.add_response(url=WRITE_URL, status_code=201)

        with patch("intelligent_router.cache_client.logger") as mock_logger:
            async with httpx.AsyncClient() as client:
                await cache_write(MESSAGES, MODEL, TASK_TYPE, RESPONSE_IMF, CACHE_URL, client)

        mock_logger.warning.assert_not_called()
