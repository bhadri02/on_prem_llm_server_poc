"""
ExactCacheService — Redis-backed exact-match cache for the Cache Service (Layer 4).

Keys are stored under the ``exact:`` prefix to isolate them from semantic cache
entries. Values are UTF-8 JSON blobs; deserialization uses ``json.loads`` with
no type coercion so the original dict structure is preserved exactly.

Validates: Requirements 4.1, 4.3, 4.7, 10.2, 10.4, 1.3, 2.4
"""

from __future__ import annotations

import json

import redis

from cache_service.exceptions import RedisUnavailableError

_KEY_PREFIX = "exact:"


class ExactCacheService:
    """
    Async exact-match cache backed by a single Redis instance.

    Keys are namespaced as ``exact:{cache_key}`` so they coexist safely with
    semantic cache entries in the same Redis keyspace.

    Args:
        redis_client: An *async* ``redis.asyncio.Redis`` client instance.
                      The caller is responsible for its lifecycle (connection
                      pool creation and teardown).
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, cache_key: str) -> dict | None:
        """
        Return the cached response for *cache_key*, or ``None`` on a miss.

        Args:
            cache_key: The SHA-256 hex digest produced by ``make_cache_key()``.

        Returns:
            The cached response ``dict`` on a hit, or ``None`` on a miss.

        Raises:
            RedisUnavailableError: If the underlying Redis call raises
                ``redis.RedisError``.
        """
        try:
            raw = await self._redis.get(f"{_KEY_PREFIX}{cache_key}")
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis read error for key '{_KEY_PREFIX}{cache_key}': {exc}",
                operation="read",
            ) from exc

        if raw is None:
            return None

        return json.loads(raw)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def set(self, cache_key: str, response: dict, ttl: int) -> None:
        """
        Persist *response* under *cache_key* with the given TTL.

        Args:
            cache_key: The SHA-256 hex digest produced by ``make_cache_key()``.
            response:  The IMF response dict to cache.
            ttl:       Time-to-live in seconds (must be a positive integer).

        Raises:
            RedisUnavailableError: If the underlying Redis call raises
                ``redis.RedisError``.
        """
        try:
            await self._redis.set(
                f"{_KEY_PREFIX}{cache_key}",
                json.dumps(response),
                ex=ttl,
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis write error for key '{_KEY_PREFIX}{cache_key}': {exc}",
                operation="write",
            ) from exc
