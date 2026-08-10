"""
api_gateway/services/key_resolver.py

Resolves an inbound X-Api-Key against the Admin Portal API
(GET /portal/keys/resolve), caching results in-process for a short TTL so
the Gateway doesn't take a network round-trip on every request.

Three outcomes for callers (api_gateway/middleware/auth.py):
  - KeyProfile returned: the key is valid — carries the resolved identity.
  - None returned: the key is not found / revoked / expired / owner inactive
    (caller returns HTTP 401).
  - KeyResolverUnavailable raised: the Admin Portal itself is unreachable
    (caller returns HTTP 503 — fail closed, never silently bypass auth).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from api_gateway.config import get_settings


@dataclass(frozen=True)
class KeyProfile:
    user_id: str
    username: str
    department: str | None
    roles: list[str]
    model_entitlements: list[str]
    key_id: str
    rate_limit_override: int | None


class KeyResolverUnavailable(Exception):
    """Raised when the Admin Portal's /portal/keys/resolve cannot be reached
    or returns something the Gateway can't interpret."""


# In-process cache: raw API key -> (expiry_monotonic, profile_or_none).
# Negative results (key not found) are cached too, so a bad key hammering
# the Gateway doesn't hammer the Admin Portal either.
_cache: dict[str, tuple[float, KeyProfile | None]] = {}


def _cache_get(key: str) -> tuple[bool, KeyProfile | None]:
    entry = _cache.get(key)
    if entry is None:
        return False, None
    expiry, value = entry
    if time.monotonic() >= expiry:
        _cache.pop(key, None)
        return False, None
    return True, value


def _cache_put(key: str, value: KeyProfile | None, ttl_seconds: float) -> None:
    _cache[key] = (time.monotonic() + ttl_seconds, value)


async def resolve_key(key: str, client: httpx.AsyncClient) -> KeyProfile | None:
    """Resolve *key* to a :class:`KeyProfile`, or ``None`` if invalid.

    Raises:
        KeyResolverUnavailable: if the Admin Portal cannot be reached or
            returns an unexpected/unparseable response.
    """
    settings = get_settings()

    hit, cached = _cache_get(key)
    if hit:
        return cached

    url = f"{settings.admin_portal_url}/portal/keys/resolve"
    try:
        response = await client.get(
            url,
            params={"key": key},
            headers={"X-Portal-Internal-Key": settings.admin_portal_internal_key},
            timeout=5.0,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
        raise KeyResolverUnavailable(str(exc)) from exc

    if response.status_code == 404:
        _cache_put(key, None, settings.key_cache_ttl_seconds)
        return None

    if response.status_code != 200:
        raise KeyResolverUnavailable(f"unexpected status {response.status_code}")

    try:
        body = response.json()
        profile = KeyProfile(
            user_id=body["user_id"],
            username=body["username"],
            department=body.get("department"),
            roles=body.get("roles") or [],
            model_entitlements=body.get("model_entitlements") or [],
            key_id=body["key_id"],
            rate_limit_override=body.get("rate_limit_override"),
        )
    except Exception as exc:
        raise KeyResolverUnavailable("invalid resolve response body") from exc

    _cache_put(key, profile, settings.key_cache_ttl_seconds)
    return profile
