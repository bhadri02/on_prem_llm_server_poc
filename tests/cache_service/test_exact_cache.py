"""
Unit tests for cache_service.services.exact_cache.ExactCacheService.
Uses fakeredis for all Redis interactions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import redis

from cache_service.exceptions import RedisUnavailableError
from cache_service.services.exact_cache import ExactCacheService


@pytest_asyncio.fixture
async def exact_cache(fake_redis):
    return ExactCacheService(fake_redis)


class TestGet:
    async def test_get_hit(self, exact_cache, fake_redis):
        """get() returns the stored dict when the key exists."""
        await fake_redis.set("exact:mykey", '{"content": "hello", "finish_reason": "stop", "usage": null}')
        result = await exact_cache.get("mykey")
        assert result == {"content": "hello", "finish_reason": "stop", "usage": None}

    async def test_get_miss(self, exact_cache):
        """get() returns None when the key does not exist."""
        result = await exact_cache.get("nonexistent_key")
        assert result is None

    async def test_set_and_get_roundtrip_preserves_types(self, exact_cache):
        """set() then get() returns the original dict with types preserved."""
        original = {
            "content": "The answer is 42",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        await exact_cache.set("roundtrip_key", original, ttl=3600)
        result = await exact_cache.get("roundtrip_key")
        assert result == original

    async def test_get_redis_error_raises_redis_unavailable(self, exact_cache, fake_redis):
        """get() raises RedisUnavailableError when Redis raises RedisError."""
        with patch.object(fake_redis, "get", new=AsyncMock(side_effect=redis.RedisError("fail"))):
            with pytest.raises(RedisUnavailableError):
                await exact_cache.get("somekey")


class TestSet:
    async def test_set_ttl_chat(self, exact_cache, fake_redis):
        """TTL is applied correctly for chat task type (3600s)."""
        await exact_cache.set("chat_key", {"content": "hi"}, ttl=3600)
        ttl = await fake_redis.ttl("exact:chat_key")
        assert 0 < ttl <= 3600

    async def test_set_ttl_code(self, exact_cache, fake_redis):
        """TTL is applied correctly for code task type (7200s)."""
        await exact_cache.set("code_key", {"content": "def foo(): pass"}, ttl=7200)
        ttl = await fake_redis.ttl("exact:code_key")
        assert 0 < ttl <= 7200

    async def test_set_ttl_summarization(self, exact_cache, fake_redis):
        """TTL is applied correctly for summarization task type (86400s)."""
        await exact_cache.set("sum_key", {"content": "summary"}, ttl=86400)
        ttl = await fake_redis.ttl("exact:sum_key")
        assert 0 < ttl <= 86400

    async def test_set_redis_error_raises_redis_unavailable(self, exact_cache, fake_redis):
        """set() raises RedisUnavailableError when Redis raises RedisError."""
        with patch.object(fake_redis, "set", new=AsyncMock(side_effect=redis.RedisError("write fail"))):
            with pytest.raises(RedisUnavailableError):
                await exact_cache.set("somekey", {"content": "x"}, ttl=60)
