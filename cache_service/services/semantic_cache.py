"""
SemanticCacheService — Redis-backed semantic cache for the Cache Service (Layer 4).

Entries are stored as JSON objects in a Redis List keyed by
``semantic_cache:{task_type}``.  Each element holds the original cache key,
the 384-dimensional sentence-transformer embedding, and the IMF response dict.

Lookup performs a full linear cosine-similarity scan over all list entries and
returns the best match whose score is >= ``settings.similarity_threshold``.

Validates: Requirements 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 2.5, 2.6, 1.4, 1.5
"""

from __future__ import annotations

import json
import math

import redis

from cache_service.config import Settings
from cache_service.exceptions import RedisUnavailableError

_LIST_PREFIX = "semantic_cache:"


class SemanticCacheService:
    """
    Async semantic cache backed by per-task-type Redis Lists.

    Each list element is a JSON-serialised dict::

        {
            "key":       "<sha256-hex>",
            "embedding": [<float>, ...],   # 384-dimensional vector
            "response":  {<IMF response>}
        }

    Args:
        redis_client: An *async* ``redis.asyncio.Redis`` client instance.
                      The caller is responsible for its lifecycle.
        settings:     The application ``Settings`` instance; provides
                      ``similarity_threshold`` and ``max_semantic_entries``.
    """

    def __init__(self, redis_client, settings: Settings) -> None:
        self._redis = redis_client
        self._settings = settings

    # ------------------------------------------------------------------
    # Pure computation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """
        Compute the cosine similarity between two vectors.

        Returns ``dot(a, b) / (norm(a) * norm(b))``.  If either vector has
        zero norm the result is ``0.0`` (rather than raising ``ZeroDivisionError``).

        Args:
            a: First vector as a list of floats.
            b: Second vector as a list of floats (must be the same length as *a*).

        Returns:
            Cosine similarity in [-1.0, 1.0], or ``0.0`` when either norm is zero.
        """
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def lookup(
        self,
        task_type: str,
        query_embedding: list[float],
    ) -> tuple[dict, float] | None:
        """
        Find the best semantic match for *query_embedding* in the given list.

        Executes ``LRANGE semantic_cache:{task_type} 0 -1``, deserialises each
        entry, and computes cosine similarity against *query_embedding*.  Returns
        the ``(response_dict, best_score)`` pair for the highest-scoring entry
        whose similarity is >= ``settings.similarity_threshold``, or ``None``
        when the list is empty or no entry meets the threshold.

        When multiple entries tie at the highest score, any one of them may be
        returned (the first encountered in iteration order).

        Args:
            task_type:       One of ``"chat"``, ``"code"``, ``"summarization"``,
                             or any other task_type label.
            query_embedding: The 384-dimensional float vector for the incoming
                             prompt.

        Returns:
            ``(response_dict, score)`` on a hit, or ``None`` on a miss.

        Raises:
            RedisUnavailableError: If the underlying Redis call raises
                ``redis.RedisError``.
        """
        redis_key = f"{_LIST_PREFIX}{task_type}"

        try:
            raw_entries: list[bytes] = await self._redis.lrange(redis_key, 0, -1)
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis read error for key '{redis_key}': {exc}",
                operation="read",
            ) from exc

        if not raw_entries:
            return None

        best_score: float = -1.0
        best_response: dict | None = None

        for raw in raw_entries:
            entry: dict = json.loads(raw)
            stored_embedding: list[float] = entry["embedding"]
            score = self._cosine_similarity(query_embedding, stored_embedding)

            if score > best_score:
                best_score = score
                best_response = entry["response"]

        if best_score >= self._settings.similarity_threshold and best_response is not None:
            return best_response, best_score

        return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(
        self,
        task_type: str,
        cache_key: str,
        embedding: list[float],
        response: dict,
    ) -> bool:
        """
        Append a new entry to the semantic cache list for *task_type*.

        First calls :meth:`get_entry_count` to check whether the list has
        reached ``settings.max_semantic_entries``.  If so, returns ``False``
        immediately (the caller is responsible for emitting the
        ``semantic_cache_full`` log event).  Otherwise serialises the entry as
        JSON and appends it via ``RPUSH``.

        Args:
            task_type:  Redis list namespace (e.g. ``"chat"``).
            cache_key:  The SHA-256 hex digest of the original prompt.
            embedding:  The 384-dimensional float vector for the prompt.
            response:   The IMF response dict to store.

        Returns:
            ``True`` when the entry was successfully appended, ``False`` when
            the list was already at capacity.

        Raises:
            RedisUnavailableError: If any underlying Redis call raises
                ``redis.RedisError``.
        """
        count = await self.get_entry_count(task_type)

        if count >= self._settings.max_semantic_entries:
            return False

        redis_key = f"{_LIST_PREFIX}{task_type}"
        entry_json = json.dumps(
            {
                "key": cache_key,
                "embedding": embedding,
                "response": response,
            }
        )

        try:
            await self._redis.rpush(redis_key, entry_json)
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis write error for key '{redis_key}': {exc}",
                operation="write",
            ) from exc

        return True

    # ------------------------------------------------------------------
    # Count
    # ------------------------------------------------------------------

    async def get_entry_count(self, task_type: str) -> int:
        """
        Return the current number of entries in the semantic cache list.

        Executes ``LLEN semantic_cache:{task_type}`` and returns the integer
        result.  Returns ``0`` when the key does not exist (Redis returns 0
        for ``LLEN`` on a non-existent key).

        Args:
            task_type: Redis list namespace (e.g. ``"chat"``).

        Returns:
            Number of entries currently stored.

        Raises:
            RedisUnavailableError: If the underlying Redis call raises
                ``redis.RedisError``.
        """
        redis_key = f"{_LIST_PREFIX}{task_type}"

        try:
            count: int = await self._redis.llen(redis_key)
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis read error for key '{redis_key}': {exc}",
                operation="read",
            ) from exc

        return count
