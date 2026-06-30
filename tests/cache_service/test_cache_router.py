"""
Integration tests for POST /cache/lookup and POST /cache/write.
Uses the app_client fixture (stub lifespan, fake redis).
"""

from __future__ import annotations

import json
import math
from unittest.mock import AsyncMock, patch

import pytest

from cache_service.exceptions import RedisUnavailableError
from cache_service.routers.cache import make_cache_key

_UNIT = 1.0 / math.sqrt(384)
_UNIT_VEC = [_UNIT] * 384

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _imf_body(
    *,
    messages=None,
    model="llama3",
    task_type="chat",
    response=None,
    request_id=None,
):
    body = {
        "request": {
            "messages": messages or [{"role": "user", "content": "Hello world"}],
            "task_type": task_type,
        },
        "routing": {"selected_model": model},
    }
    if response is not None:
        body["response"] = response
    if request_id is not None:
        body["request_id"] = request_id
    return body


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------

class TestLookupExact:
    async def test_lookup_exact_hit(self, app_client, fake_redis):
        """Returns hit=true, cache_type=exact when exact key exists."""
        msgs = [{"role": "user", "content": "Hello world"}]
        key = make_cache_key(msgs, "llama3", "chat")
        payload = json.dumps({"content": "cached response", "finish_reason": "stop", "usage": None})
        await fake_redis.set(f"exact:{key}", payload)

        resp = await app_client.post("/cache/lookup", json=_imf_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is True
        assert data["cache_type"] == "exact"
        assert data["response"]["content"] == "cached response"


class TestLookupSemantic:
    async def test_lookup_semantic_hit(self, app_client, fake_redis, mock_embedding_generator):
        """Returns hit=true, cache_type=semantic when semantic match found."""
        stored_response = {"content": "semantic cached", "finish_reason": "stop", "usage": None}
        entry = json.dumps({"key": "k1", "embedding": _UNIT_VEC, "response": stored_response})
        await fake_redis.rpush("semantic_cache:chat", entry)

        # mock_embedding_generator already returns _UNIT_VEC — similarity will be 1.0
        resp = await app_client.post("/cache/lookup", json=_imf_body(messages=[{"role": "user", "content": "Totally different query"}]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is True
        assert data["cache_type"] == "semantic"
        assert data["similarity_score"] >= 0.90


class TestLookupMiss:
    async def test_lookup_miss(self, app_client):
        """Returns hit=false when no exact or semantic match exists."""
        resp = await app_client.post("/cache/lookup", json=_imf_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is False
        assert data["cache_type"] is None
        assert data["response"] is None


class TestLookupValidation:
    async def test_lookup_missing_messages_returns_422(self, app_client):
        """Returns 422 when messages field is missing."""
        body = {"request": {"task_type": "chat"}, "routing": {"selected_model": "llama3"}}
        resp = await app_client.post("/cache/lookup", json=body)
        assert resp.status_code == 422

    async def test_lookup_missing_selected_model_returns_422(self, app_client):
        """Returns 422 when routing.selected_model is missing."""
        body = {
            "request": {"messages": [{"role": "user", "content": "hi"}], "task_type": "chat"},
            "routing": {},
        }
        resp = await app_client.post("/cache/lookup", json=body)
        assert resp.status_code == 422

    async def test_lookup_missing_task_type_returns_422(self, app_client):
        """Returns 422 when request.task_type is missing."""
        body = {
            "request": {"messages": [{"role": "user", "content": "hi"}]},
            "routing": {"selected_model": "llama3"},
        }
        resp = await app_client.post("/cache/lookup", json=body)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Write tests
# ---------------------------------------------------------------------------

class TestWrite:
    async def test_write_success(self, app_client):
        """Returns written=true and a 64-char cache_key on success."""
        body = _imf_body(response={"content": "answer", "finish_reason": "stop"})
        resp = await app_client.post("/cache/write", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["written"] is True
        assert len(data["cache_key"]) == 64

    async def test_write_null_response_returns_422(self, app_client):
        """Returns 422 when response field is null/missing."""
        body = _imf_body()  # no response field
        resp = await app_client.post("/cache/write", json=body)
        assert resp.status_code == 422

    async def test_write_redis_unavailable_returns_503(self, app_client, fake_redis):
        """Returns 503 when Redis is unavailable during exact cache write."""
        import redis as redis_lib
        with patch.object(fake_redis, "set", new=AsyncMock(side_effect=redis_lib.RedisError("write fail"))):
            body = _imf_body(response={"content": "data", "finish_reason": "stop"})
            resp = await app_client.post("/cache/write", json=body)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Cache block tests
# ---------------------------------------------------------------------------

class TestCacheBlock:
    async def test_cache_block_has_exactly_four_keys(self, app_client, fake_redis):
        """
        After a lookup hit the LookupResponse has hit, cache_key, cache_type,
        and similarity_score (4 keys total).
        """
        msgs = [{"role": "user", "content": "block test"}]
        key = make_cache_key(msgs, "llama3", "chat")
        await fake_redis.set(
            f"exact:{key}",
            json.dumps({"content": "block", "finish_reason": "stop", "usage": None}),
        )

        resp = await app_client.post("/cache/lookup", json=_imf_body(messages=msgs))
        assert resp.status_code == 200
        data = resp.json()
        # LookupResponse has exactly these four top-level keys
        assert set(data.keys()) == {"hit", "cache_key", "cache_type", "response", "similarity_score"}

    async def test_cache_block_wholesale_replacement(self, app_client, fake_redis):
        """
        The cache block fields are freshly derived on every lookup — not merged
        from the incoming IMF body.
        """
        msgs = [{"role": "user", "content": "replacement test"}]
        key = make_cache_key(msgs, "llama3", "chat")
        await fake_redis.set(
            f"exact:{key}",
            json.dumps({"content": "fresh", "finish_reason": "stop", "usage": None}),
        )

        body = _imf_body(messages=msgs)
        # Provide an old stale cache block in the IMF body (should be ignored)
        body["cache"] = {"lookup_hit": False, "cache_key": "stale_key", "cache_type": None, "similarity_score": None}

        resp = await app_client.post("/cache/lookup", json=body)
        assert resp.status_code == 200
        data = resp.json()
        # Should reflect the fresh lookup, not the stale body
        assert data["hit"] is True
        assert data["cache_key"] == key
        assert data["cache_type"] == "exact"
