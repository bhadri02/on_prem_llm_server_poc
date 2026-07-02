"""
tests/unit/test_health_checker.py

Unit tests for intelligent_router.health_checker.check_model_health.

Covers:
  - HTTP 200 returns True
  - HTTP 503 returns False
  - HTTP 301 redirect returns False (follow_redirects=False)
  - Timeout (httpx.TimeoutException) returns False
  - Connection refused (httpx.ConnectError) returns False

pytest-httpx (httpx_mock fixture) intercepts all httpx calls made by the
AsyncClient passed to check_model_health, so no real network calls are made.

asyncio_mode = auto in pytest.ini — no @pytest.mark.asyncio needed.
"""

import httpx
import pytest

from intelligent_router.health_checker import check_model_health

HEALTH_URL = "http://inference-ollama:11434/api/tags"
TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# HTTP 200 — healthy
# ---------------------------------------------------------------------------


class TestHttp200ReturnsTrue:
    async def test_returns_true_for_200(self, httpx_mock):
        httpx_mock.add_response(url=HEALTH_URL, status_code=200)

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is True


# ---------------------------------------------------------------------------
# HTTP 503 — non-200 status
# ---------------------------------------------------------------------------


class TestHttp503ReturnsFalse:
    async def test_returns_false_for_503(self, httpx_mock):
        httpx_mock.add_response(url=HEALTH_URL, status_code=503)

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is False


# ---------------------------------------------------------------------------
# HTTP 301 redirect — follow_redirects=False means 3xx is returned as-is
# ---------------------------------------------------------------------------


class TestHttp301ReturnsFalse:
    async def test_returns_false_for_301(self, httpx_mock):
        httpx_mock.add_response(url=HEALTH_URL, status_code=301)

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is False


# ---------------------------------------------------------------------------
# Timeout — httpx.TimeoutException
# ---------------------------------------------------------------------------


class TestTimeoutReturnsFalse:
    async def test_returns_false_on_connect_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectTimeout("Connection timed out", request=None),
            url=HEALTH_URL,
        )

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is False

    async def test_returns_false_on_read_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ReadTimeout("Read timed out", request=None),
            url=HEALTH_URL,
        )

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is False


# ---------------------------------------------------------------------------
# Connection refused — httpx.ConnectError
# ---------------------------------------------------------------------------


class TestConnectionRefusedReturnsFalse:
    async def test_returns_false_on_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=HEALTH_URL,
        )

        async with httpx.AsyncClient() as client:
            result = await check_model_health(HEALTH_URL, client, TIMEOUT)

        assert result is False
