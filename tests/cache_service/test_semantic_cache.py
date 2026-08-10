"""
Unit tests for cache_service.services.semantic_cache.SemanticCacheService.
Uses fakeredis for all Redis interactions.
"""

from __future__ import annotations

import json
import math
import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import redis

from cache_service.config import Settings
from cache_service.exceptions import RedisUnavailableError
from cache_service.services.semantic_cache import SemanticCacheService

# 384-dim unit vector
_UNIT = 1.0 / math.sqrt(384)
_UNIT_VEC = [_UNIT] * 384

# A slightly different vector (still above threshold)
_SIMILAR_VEC = [_UNIT * 1.001 if i % 2 == 0 else _UNIT for i in range(384)]

# Orthogonal-ish vector (low similarity)
_DIFF_VEC = [_UNIT if i < 192 else -_UNIT for i in range(384)]


@pytest_asyncio.fixture
async def sem_cache(fake_redis):
    settings = Settings(similarity_threshold=0.90, max_semantic_entries=5)
    return SemanticCacheService(fake_redis, settings)


@pytest_asyncio.fixture
async def sem_cache_short_ttl(fake_redis):
    """60-second TTL (the real default) for expiry tests."""
    settings = Settings(similarity_threshold=0.90, max_semantic_entries=5, cache_ttl_seconds=60)
    return SemanticCacheService(fake_redis, settings)


@pytest_asyncio.fixture
async def sem_cache_small(fake_redis):
    """Semantic cache with capacity=2 for capacity-test scenarios."""
    settings = Settings(similarity_threshold=0.90, max_semantic_entries=2)
    return SemanticCacheService(fake_redis, settings)


class TestLookup:
    async def test_lookup_empty_list_returns_none(self, sem_cache):
        """lookup() returns None when the Redis list is empty."""
        result = await sem_cache.lookup("chat", _UNIT_VEC)
        assert result is None

    async def test_lookup_above_threshold_returns_hit(self, sem_cache, fake_redis):
        """lookup() returns (response, score) when best score >= threshold."""
        entry = {"key": "abc", "embedding": _UNIT_VEC, "response": {"content": "cached", "finish_reason": "stop", "usage": None}}
        await fake_redis.rpush("semantic_cache:chat", json.dumps(entry))

        result = await sem_cache.lookup("chat", _UNIT_VEC)
        assert result is not None
        response_dict, score = result
        assert response_dict == entry["response"]
        assert score >= 0.90

    async def test_lookup_below_threshold_returns_none(self, sem_cache, fake_redis):
        """lookup() returns None when best score < threshold."""
        entry = {
            "key": "low_sim",
            "embedding": _DIFF_VEC,
            "response": {"content": "distant", "finish_reason": "stop", "usage": None},
        }
        await fake_redis.rpush("semantic_cache:chat", json.dumps(entry))

        result = await sem_cache.lookup("chat", _UNIT_VEC)
        assert result is None

    async def test_lookup_returns_highest_scoring_entry(self, sem_cache, fake_redis):
        """lookup() returns the entry with the highest cosine similarity."""
        response_low = {"content": "low", "finish_reason": "stop", "usage": None}
        response_high = {"content": "high", "finish_reason": "stop", "usage": None}

        entry_low = {"key": "k1", "embedding": _DIFF_VEC, "response": response_low}
        entry_high = {"key": "k2", "embedding": _UNIT_VEC, "response": response_high}

        await fake_redis.rpush("semantic_cache:chat", json.dumps(entry_low))
        await fake_redis.rpush("semantic_cache:chat", json.dumps(entry_high))

        result = await sem_cache.lookup("chat", _UNIT_VEC)
        assert result is not None
        response_dict, score = result
        assert response_dict["content"] == "high"


class TestExpiry:
    """Semantic entries older than cache_ttl_seconds are treated as misses,
    matching the exact cache's TTL behavior (Req: cache hits only within 1
    minute, then a miss — uniformly, not just for the exact-match path)."""

    async def test_expired_entry_is_not_returned_even_above_threshold(
        self, sem_cache_short_ttl, fake_redis
    ):
        stale_entry = {
            "key": "old",
            "embedding": _UNIT_VEC,
            "response": {"content": "stale", "finish_reason": "stop", "usage": None},
            "timestamp": time.time() - 61,  # 1 second past the 60s TTL
        }
        await fake_redis.rpush("semantic_cache:chat", json.dumps(stale_entry))

        result = await sem_cache_short_ttl.lookup("chat", _UNIT_VEC)
        assert result is None

    async def test_fresh_entry_within_ttl_is_still_returned(self, sem_cache_short_ttl, fake_redis):
        fresh_entry = {
            "key": "new",
            "embedding": _UNIT_VEC,
            "response": {"content": "fresh", "finish_reason": "stop", "usage": None},
            "timestamp": time.time() - 5,  # well within the 60s TTL
        }
        await fake_redis.rpush("semantic_cache:chat", json.dumps(fresh_entry))

        result = await sem_cache_short_ttl.lookup("chat", _UNIT_VEC)
        assert result is not None
        response_dict, score = result
        assert response_dict["content"] == "fresh"

    async def test_expired_entry_is_lazily_evicted_from_the_list(
        self, sem_cache_short_ttl, fake_redis
    ):
        stale_entry = {
            "key": "old",
            "embedding": _UNIT_VEC,
            "response": {"content": "stale", "finish_reason": "stop", "usage": None},
            "timestamp": time.time() - 120,
        }
        await fake_redis.rpush("semantic_cache:chat", json.dumps(stale_entry))
        assert await fake_redis.llen("semantic_cache:chat") == 1

        await sem_cache_short_ttl.lookup("chat", _UNIT_VEC)

        assert await fake_redis.llen("semantic_cache:chat") == 0

    async def test_entry_without_timestamp_is_treated_as_fresh(self, sem_cache_short_ttl, fake_redis):
        """Legacy/hand-built entries with no "timestamp" field (as used by
        the other tests in this file) must not be punished by the new
        expiry check — only entries that actually carry an aged timestamp
        expire."""
        legacy_entry = {
            "key": "no-ts",
            "embedding": _UNIT_VEC,
            "response": {"content": "legacy", "finish_reason": "stop", "usage": None},
        }
        await fake_redis.rpush("semantic_cache:chat", json.dumps(legacy_entry))

        result = await sem_cache_short_ttl.lookup("chat", _UNIT_VEC)
        assert result is not None
        assert result[0]["content"] == "legacy"


class TestWrite:
    async def test_write_below_capacity_returns_true(self, sem_cache_small, fake_redis):
        """write() returns True when list is below max_semantic_entries."""
        result = await sem_cache_small.write("chat", "key1", _UNIT_VEC, {"content": "r1"})
        assert result is True

    async def test_write_at_capacity_returns_false(self, sem_cache_small, fake_redis):
        """write() returns False when list has reached max_semantic_entries."""
        # Fill to capacity (max=2)
        await sem_cache_small.write("chat", "key1", _UNIT_VEC, {"content": "r1"})
        await sem_cache_small.write("chat", "key2", _UNIT_VEC, {"content": "r2"})

        result = await sem_cache_small.write("chat", "key3", _UNIT_VEC, {"content": "r3"})
        assert result is False

    async def test_write_increments_llen(self, sem_cache, fake_redis):
        """write() appends to the Redis list, incrementing LLEN."""
        assert await fake_redis.llen("semantic_cache:chat") == 0
        await sem_cache.write("chat", "key1", _UNIT_VEC, {"content": "r1"})
        assert await fake_redis.llen("semantic_cache:chat") == 1
        await sem_cache.write("chat", "key2", _UNIT_VEC, {"content": "r2"})
        assert await fake_redis.llen("semantic_cache:chat") == 2

    async def test_write_stamps_entry_with_current_timestamp(self, sem_cache, fake_redis):
        """write() stamps every entry with a "timestamp" close to now, so
        lookup() can age it out after cache_ttl_seconds."""
        before = time.time()
        await sem_cache.write("chat", "key1", _UNIT_VEC, {"content": "r1"})
        after = time.time()

        raw = (await fake_redis.lrange("semantic_cache:chat", 0, -1))[0]
        entry = json.loads(raw)
        assert before <= entry["timestamp"] <= after


class TestCosineSimilarity:
    def test_cosine_similarity_identical_vectors(self):
        """Cosine similarity of a vector with itself is 1.0."""
        vec = [1.0, 0.0, 0.0]
        result = SemanticCacheService._cosine_similarity(vec, vec)
        assert abs(result - 1.0) < 1e-9

    def test_cosine_similarity_zero_vector(self):
        """Cosine similarity returns 0.0 when either vector is zero."""
        zero = [0.0, 0.0, 0.0]
        vec = [1.0, 2.0, 3.0]
        assert SemanticCacheService._cosine_similarity(zero, vec) == 0.0
        assert SemanticCacheService._cosine_similarity(vec, zero) == 0.0
