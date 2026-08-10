"""
Unit tests for api_gateway.services.key_resolver — Phase 2 (RBAC + per-user
API keys) identity resolution against the Admin Portal, with in-process
caching of both positive and negative results.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from api_gateway.services.key_resolver import (
    KeyProfile,
    KeyResolverUnavailable,
    _cache,
    resolve_key,
)

_RESOLVE_URL = "http://admin-portal:8084/portal/keys/resolve"

_VALID_BODY = {
    "user_id": "u1",
    "username": "alice",
    "department": "eng",
    "roles": ["developer"],
    "model_entitlements": [],
    "key_id": "k1",
    "rate_limit_override": None,
}


@pytest.fixture(autouse=True)
def gateway_settings(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", "http://security:8081")
    monkeypatch.setenv("ADMIN_PORTAL_URL", "http://admin-portal:8084")
    monkeypatch.setenv("ADMIN_PORTAL_INTERNAL_KEY", "internal-key")
    monkeypatch.setenv("KEY_CACHE_TTL_SECONDS", "30")

    from api_gateway.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_key_resolver_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.mark.asyncio
@respx.mock
async def test_resolve_valid_key_returns_profile():
    respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(200, json=_VALID_BODY))

    async with httpx.AsyncClient() as client:
        profile = await resolve_key("some-key", client)

    assert isinstance(profile, KeyProfile)
    assert profile.user_id == "u1"
    assert profile.roles == ["developer"]
    assert profile.model_entitlements == []


@pytest.mark.asyncio
@respx.mock
async def test_internal_key_header_is_sent():
    route = respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(200, json=_VALID_BODY))

    async with httpx.AsyncClient() as client:
        await resolve_key("some-key", client)

    sent_request = route.calls[0].request
    assert sent_request.headers["X-Portal-Internal-Key"] == "internal-key"
    assert sent_request.url.params["key"] == "some-key"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_not_found_returns_none():
    respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(404, json={"error": "key_not_found"}))

    async with httpx.AsyncClient() as client:
        profile = await resolve_key("bad-key", client)

    assert profile is None


@pytest.mark.asyncio
@respx.mock
async def test_admin_portal_connect_error_raises_unavailable():
    respx.get(_RESOLVE_URL).mock(side_effect=httpx.ConnectError("boom"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(KeyResolverUnavailable):
            await resolve_key("some-key", client)


@pytest.mark.asyncio
@respx.mock
async def test_admin_portal_timeout_raises_unavailable():
    respx.get(_RESOLVE_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(KeyResolverUnavailable):
            await resolve_key("some-key", client)


@pytest.mark.asyncio
@respx.mock
async def test_unexpected_status_raises_unavailable():
    respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        with pytest.raises(KeyResolverUnavailable):
            await resolve_key("some-key", client)


@pytest.mark.asyncio
@respx.mock
async def test_positive_result_is_cached_within_ttl():
    route = respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(200, json=_VALID_BODY))

    async with httpx.AsyncClient() as client:
        await resolve_key("some-key", client)
        await resolve_key("some-key", client)

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_negative_result_is_also_cached():
    route = respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(404, json={"error": "key_not_found"}))

    async with httpx.AsyncClient() as client:
        await resolve_key("bad-key", client)
        await resolve_key("bad-key", client)

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_different_keys_are_cached_independently():
    route = respx.get(_RESOLVE_URL).mock(return_value=httpx.Response(200, json=_VALID_BODY))

    async with httpx.AsyncClient() as client:
        await resolve_key("key-a", client)
        await resolve_key("key-b", client)

    assert route.call_count == 2
