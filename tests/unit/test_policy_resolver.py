"""
Unit tests for intelligent_router/services/policy_resolver.py.

Covers: successful fetch, TTL-cache reuse, fallback to the static YAML
matrix on failure, backoff (no retry-storm during an outage), and recovery
once admin_portal becomes reachable again.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from intelligent_router.policy import PolicyMatrix
from intelligent_router.services.policy_resolver import get_policy_matrix, reset_cache


def _response(status_code, json_body):
    """httpx.Response requires an attached request for raise_for_status()
    to work — plain httpx.Response(status, json=...) has none."""
    return httpx.Response(
        status_code, json=json_body, request=httpx.Request("GET", "http://admin-portal:8084/portal/policy/matrix")
    )


def _make_state(http_client, ttl=15, static_matrix=None):
    settings = MagicMock()
    settings.policy_cache_ttl_seconds = ttl
    settings.admin_portal_url = "http://admin-portal:8084"
    settings.admin_portal_internal_key = "test-internal-key"

    return types.SimpleNamespace(
        settings=settings,
        http_client=http_client,
        policy_matrix=static_matrix or PolicyMatrix(roles={"admin": {"chat": True}}),
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


class TestSuccessfulFetch:
    async def test_returns_live_matrix_on_success(self):
        client = AsyncMock()
        client.get.return_value = _response(200, {"viewer": {"chat": True}, "admin": {"chat": True, "code": True}})
        state = _make_state(client)

        matrix = await get_policy_matrix(state)

        assert matrix.roles == {"viewer": {"chat": True}, "admin": {"chat": True, "code": True}}
        client.get.assert_awaited_once()

    async def test_sends_internal_key_header(self):
        client = AsyncMock()
        client.get.return_value = _response(200, {"admin": {"chat": True}})
        state = _make_state(client)

        await get_policy_matrix(state)

        _, kwargs = client.get.call_args
        assert kwargs["headers"]["X-Portal-Internal-Key"] == "test-internal-key"

    async def test_second_call_within_ttl_uses_cache_not_a_new_request(self):
        client = AsyncMock()
        client.get.return_value = _response(200, {"admin": {"chat": True}})
        state = _make_state(client, ttl=1000)

        await get_policy_matrix(state)
        await get_policy_matrix(state)

        assert client.get.await_count == 1


class TestFailureFallback:
    async def test_falls_back_to_static_matrix_on_connect_error(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("connection refused")
        static = PolicyMatrix(roles={"admin": {"chat": True}})
        state = _make_state(client, static_matrix=static)

        matrix = await get_policy_matrix(state)

        assert matrix is static

    async def test_falls_back_on_non_200_response(self):
        client = AsyncMock()
        client.get.return_value = _response(401, {"detail": "unauthorized"})
        static = PolicyMatrix(roles={"admin": {"chat": True}})
        state = _make_state(client, static_matrix=static)

        matrix = await get_policy_matrix(state)

        assert matrix is static

    async def test_falls_back_on_empty_response_body(self):
        client = AsyncMock()
        client.get.return_value = _response(200, {})
        static = PolicyMatrix(roles={"admin": {"chat": True}})
        state = _make_state(client, static_matrix=static)

        matrix = await get_policy_matrix(state)

        assert matrix is static

    async def test_does_not_retry_within_ttl_after_a_failure(self):
        """A persistent outage should back off for a full TTL window, not
        retry on every single request."""
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("connection refused")
        state = _make_state(client, ttl=1000)

        await get_policy_matrix(state)
        await get_policy_matrix(state)
        await get_policy_matrix(state)

        assert client.get.await_count == 1

    async def test_recovers_once_admin_portal_is_reachable_again(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("connection refused")
        state = _make_state(client, ttl=0)  # TTL=0 → always attempt a fresh fetch

        first = await get_policy_matrix(state)
        assert first is state.policy_matrix  # fell back

        client.get.side_effect = None
        client.get.return_value = _response(200, {"admin": {"chat": True, "code": True}})

        second = await get_policy_matrix(state)
        assert second.roles == {"admin": {"chat": True, "code": True}}


class TestDefensiveAgainstMockedTestState:
    async def test_survives_non_numeric_ttl_settings(self):
        """Some test fixtures across this codebase build app.state with a
        plain MagicMock() for settings, which has no real numeric
        policy_cache_ttl_seconds — this must fall back gracefully, not
        raise, since the TTL arithmetic itself would TypeError."""
        state = types.SimpleNamespace(
            settings=MagicMock(),  # policy_cache_ttl_seconds is a MagicMock, not an int
            http_client=MagicMock(),
            policy_matrix=PolicyMatrix(roles={"admin": {"chat": True}}),
        )

        matrix = await get_policy_matrix(state)

        assert matrix is state.policy_matrix
