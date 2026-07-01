"""
Cache router module for the Cache Service (Layer 4).

Exposes:
  make_cache_key      — standalone SHA-256 helper (Requirement 1.2, 2.3, 10.1)
  router              — APIRouter(prefix="/cache") with two endpoints:
                          POST /cache/lookup  (Requirements 1.1–1.10, 3.x, 5.x, 7.x, 9.x)
                          POST /cache/write   (Requirements 2.1–2.10, 3.x, 5.x, 7.x, 9.x)

Services are retrieved from ``request.app.state`` (populated during lifespan in Task 12):
  request.app.state.exact_cache        — ExactCacheService instance
  request.app.state.semantic_cache     — SemanticCacheService instance
  request.app.state.embedding_generator — EmbeddingGenerator instance

Validates: Requirements 1.1–1.10, 2.1–2.10, 3.1–3.6, 4.1–4.7, 5.3–5.8,
           7.1–7.4, 9.2–9.5, 10.1
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cache_service.config import get_settings
from cache_service.exceptions import EmbeddingEncodeError, RedisUnavailableError
from cache_service.metrics import (
    llm_cache_errors_total,
    llm_cache_latency_seconds,
    llm_cache_requests_total,
    llm_cache_semantic_entries,
)
from cache_service.schemas.cache import CacheBlock, LookupResponse, WriteResponse
from cache_service.schemas.imf import IMFDocument, IMFResponse


# ---------------------------------------------------------------------------
# Cache key derivation helper
# ---------------------------------------------------------------------------

def make_cache_key(messages: list[dict], model: str, task_type: str) -> str:
    """Return the SHA-256 hex digest that uniquely identifies a cache entry.

    The key is derived by:
      1. Stripping leading/trailing whitespace from each message's ``content``.
      2. Joining all stripped content values with a single space.
      3. Lower-casing the joined string.
      4. Appending ``|{model}|{task_type}`` to form the raw key material.
      5. UTF-8–encoding and SHA-256–hashing the raw string.

    This produces identical keys for any two requests whose messages (after
    per-message whitespace stripping), model, and task_type are equal —
    regardless of surrounding whitespace on the full prompt.

    Args:
        messages:  List of message dicts, each containing at minimum a
                   ``"content"`` key (e.g. ``[{"role": "user", "content": "…"}]``).
        model:     The selected model identifier (``routing.selected_model``).
        task_type: The request task type (``request.task_type``).

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    content = " ".join(m["content"].strip() for m in messages).lower().strip()
    raw = f"{content}|{model}|{task_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------

def _log(event: dict[str, Any]) -> None:
    """Emit a structured JSON log entry to stdout.  Silently discards on error."""
    try:
        print(json.dumps(event), flush=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/cache", tags=["cache"])


# ---------------------------------------------------------------------------
# POST /cache/lookup
# ---------------------------------------------------------------------------

@router.post("/lookup", response_model=LookupResponse)
async def lookup(imf: IMFDocument, request: Request) -> Any:
    """
    Exact + semantic cache lookup.

    Returns a ``LookupResponse`` describing whether a hit was found, the
    cache key, cache type, stored response, and (for semantic hits) the
    similarity score.

    Validates: Requirements 1.1–1.10, 3.1–3.6, 5.3–5.8, 7.1–7.4, 9.2–9.4
    """
    start_time = time.monotonic()
    settings = get_settings()
    request_id: str = imf.request_id or "unknown"
    task_type: str = imf.request.task_type

    # Retrieve service instances from app.state
    exact_cache = getattr(request.app.state, "exact_cache", None)
    semantic_cache = getattr(request.app.state, "semantic_cache", None)
    embedding_generator = getattr(request.app.state, "embedding_generator", None)

    # --- Derive cache key and prompt text ---
    messages_as_dicts = [{"content": m.content, "role": m.role} for m in imf.request.messages]
    cache_key = make_cache_key(
        messages_as_dicts,
        imf.routing.selected_model,
        task_type,
    )
    prompt_text = " ".join(m.content.strip() for m in imf.request.messages).lower().strip()

    # Helper: build miss response
    def _miss_response() -> LookupResponse:
        return LookupResponse(
            hit=False,
            cache_key=cache_key,
            cache_type=None,
            response=None,
            similarity_score=None,
        )

    # Helper: observe metrics + emit log on exit
    def _finish(hit: bool, cache_type_val: str | None, similarity: float | None) -> None:
        latency_s = time.monotonic() - start_time
        latency_ms = int(latency_s * 1000)
        status = "hit" if hit else "miss"
        ct_label = cache_type_val if cache_type_val else "none"
        llm_cache_requests_total.labels(
            status=status, cache_type=ct_label, task_type=task_type
        ).inc()
        llm_cache_latency_seconds.labels(operation="lookup", task_type=task_type).observe(latency_s)

        if hit:
            _log(
                {
                    "event": "cache_hit",
                    "request_id": request_id,
                    "cache_type": cache_type_val,
                    "cache_key": cache_key,
                    "task_type": task_type,
                    "similarity_score": similarity,
                    "latency_ms": latency_ms,
                }
            )
        else:
            _log(
                {
                    "event": "cache_miss",
                    "request_id": request_id,
                    "cache_key": cache_key,
                    "task_type": task_type,
                    "latency_ms": latency_ms,
                }
            )

    # ------------------------------------------------------------------
    # 1. Exact cache lookup
    # ------------------------------------------------------------------
    if exact_cache is not None:
        try:
            exact_hit = await exact_cache.get(cache_key)
        except RedisUnavailableError:
            # Redis unavailable before any hit — return miss (Req 1.8)
            llm_cache_errors_total.labels(
                error_code="redis_unavailable", operation="lookup"
            ).inc()
            _log(
                {
                    "event": "redis_unavailable",
                    "request_id": request_id,
                    "operation": "lookup",
                    "cache_key": cache_key,
                    "task_type": task_type,
                }
            )
            _finish(hit=False, cache_type_val=None, similarity=None)
            return _miss_response()

        if exact_hit is not None:
            # Exact HIT (Req 1.3)
            _finish(hit=True, cache_type_val="exact", similarity=None)
            return LookupResponse(
                hit=True,
                cache_key=cache_key,
                cache_type="exact",
                response=IMFResponse(**exact_hit),
                similarity_score=None,
            )
    # exact_cache is None or exact miss — continue to semantic

    # ------------------------------------------------------------------
    # 2. Embedding generation (Req 1.4)
    # ------------------------------------------------------------------
    if embedding_generator is not None:
        try:
            query_embedding = embedding_generator.encode(prompt_text)
        except EmbeddingEncodeError:
            # Embedding failure during lookup — return miss (Req 1.9)
            llm_cache_errors_total.labels(
                error_code="embedding_error", operation="lookup"
            ).inc()
            _log(
                {
                    "event": "embedding_error",
                    "request_id": request_id,
                    "operation": "lookup",
                    "cache_key": cache_key,
                    "task_type": task_type,
                }
            )
            _finish(hit=False, cache_type_val=None, similarity=None)
            return _miss_response()
    else:
        # No embedding generator available — semantic lookup not possible
        _finish(hit=False, cache_type_val=None, similarity=None)
        return _miss_response()

    # ------------------------------------------------------------------
    # 3. Semantic cache lookup (Req 1.4, 1.5)
    # ------------------------------------------------------------------
    if semantic_cache is not None:
        try:
            semantic_result = await semantic_cache.lookup(task_type, query_embedding)
        except RedisUnavailableError:
            # Redis failure during semantic phase — treat as miss
            llm_cache_errors_total.labels(
                error_code="redis_unavailable", operation="lookup"
            ).inc()
            _log(
                {
                    "event": "redis_unavailable",
                    "request_id": request_id,
                    "operation": "lookup",
                    "cache_key": cache_key,
                    "task_type": task_type,
                }
            )
            _finish(hit=False, cache_type_val=None, similarity=None)
            return _miss_response()

        if semantic_result is not None:
            response_dict, score = semantic_result
            # Semantic HIT (Req 1.5)
            _finish(hit=True, cache_type_val="semantic", similarity=score)
            return LookupResponse(
                hit=True,
                cache_key=cache_key,
                cache_type="semantic",
                response=IMFResponse(**response_dict),
                similarity_score=score,
            )

    # ------------------------------------------------------------------
    # 4. Miss (Req 1.6)
    # ------------------------------------------------------------------
    _finish(hit=False, cache_type_val=None, similarity=None)
    return _miss_response()


# ---------------------------------------------------------------------------
# POST /cache/write
# ---------------------------------------------------------------------------

@router.post("/write", response_model=WriteResponse)
async def write(imf: IMFDocument, request: Request) -> Any:
    """
    Write an IMF response into the exact and semantic caches.

    Returns HTTP 422 if the ``response`` field is null/absent.
    Returns HTTP 503 if Redis is unavailable during the exact write.
    Returns HTTP 200 ``{written: true, cache_key: ...}`` on success, even if
    the semantic write is skipped due to embedding failure or capacity.

    Validates: Requirements 2.1–2.10, 3.x, 5.3–5.8, 7.3–7.4, 9.2–9.5
    """
    start_time = time.monotonic()
    settings = get_settings()
    request_id: str = imf.request_id or "unknown"
    task_type: str = imf.request.task_type

    # Retrieve service instances from app.state
    exact_cache = getattr(request.app.state, "exact_cache", None)
    semantic_cache = getattr(request.app.state, "semantic_cache", None)
    embedding_generator = getattr(request.app.state, "embedding_generator", None)

    # ------------------------------------------------------------------
    # Validate response field (Req 2.7)
    # ------------------------------------------------------------------
    if imf.response is None:
        _log(
            {
                "event": "cache_write_invalid",
                "request_id": request_id,
                "reason": "response field null or absent",
            }
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "cache_write_invalid",
                "request_id": request_id,
                "reason": "response field null or absent",
            },
        )

    # --- Derive cache key, prompt text, TTL ---
    messages_as_dicts = [{"content": m.content, "role": m.role} for m in imf.request.messages]
    cache_key = make_cache_key(
        messages_as_dicts,
        imf.routing.selected_model,
        task_type,
    )
    prompt_text = " ".join(m.content.strip() for m in imf.request.messages).lower().strip()

    ttl_map = {
        "chat": settings.ttl_chat,
        "code": settings.ttl_code,
        "summarization": settings.ttl_summarization,
    }
    ttl: int = ttl_map.get(task_type, 3600)

    response_dict: dict = imf.response.model_dump()

    # ------------------------------------------------------------------
    # 1. Exact cache write (Req 2.4, 4.1, 4.2)
    # ------------------------------------------------------------------
    if exact_cache is not None:
        try:
            await exact_cache.set(cache_key, response_dict, ttl)
        except RedisUnavailableError:
            # Redis unavailable on write → 503 (Req 2.8)
            latency_ms = int((time.monotonic() - start_time) * 1000)
            llm_cache_errors_total.labels(
                error_code="redis_unavailable", operation="write"
            ).inc()
            _log(
                {
                    "event": "redis_unavailable",
                    "request_id": request_id,
                    "operation": "write",
                    "cache_key": cache_key,
                    "task_type": task_type,
                    "latency_ms": latency_ms,
                }
            )
            return JSONResponse(
                status_code=503,
                content={
                    "event": "redis_unavailable",
                    "request_id": request_id,
                    "operation": "write",
                },
            )

    # ------------------------------------------------------------------
    # 2. Embedding generation for semantic write (Req 2.5)
    # ------------------------------------------------------------------
    embedding: list[float] | None = None
    if embedding_generator is not None:
        try:
            embedding = embedding_generator.encode(prompt_text)
        except EmbeddingEncodeError:
            # Embedding failure — skip semantic write, but exact already succeeded (Req 2.9)
            _log(
                {
                    "event": "embedding_error",
                    "request_id": request_id,
                    "operation": "write",
                    "cache_key": cache_key,
                    "task_type": task_type,
                }
            )
            llm_cache_errors_total.labels(
                error_code="embedding_error", operation="write"
            ).inc()
            embedding = None

    # ------------------------------------------------------------------
    # 3. Semantic cache write (Req 2.5, 2.6, 5.6, 5.8)
    # ------------------------------------------------------------------
    if embedding is not None and semantic_cache is not None:
        try:
            written_semantic = await semantic_cache.write(
                task_type, cache_key, embedding, response_dict
            )
        except RedisUnavailableError:
            # Semantic write Redis failure — log but don't fail the overall write
            _log(
                {
                    "event": "redis_unavailable",
                    "request_id": request_id,
                    "operation": "write",
                    "cache_key": cache_key,
                    "task_type": task_type,
                }
            )
            llm_cache_errors_total.labels(
                error_code="redis_unavailable", operation="write"
            ).inc()
            written_semantic = False
        else:
            if not written_semantic:
                # Semantic cache full (Req 2.6, 5.8, 7.4)
                _log(
                    {
                        "event": "semantic_cache_full",
                        "request_id": request_id,
                        "task_type": task_type,
                        "cache_key": cache_key,
                    }
                )

        # Update gauge metric with current semantic entry count (Req 9.5)
        if semantic_cache is not None:
            try:
                entry_count = await semantic_cache.get_entry_count(task_type)
                llm_cache_semantic_entries.labels(task_type=task_type).set(entry_count)
            except RedisUnavailableError:
                pass  # Non-critical; don't fail the write

    # ------------------------------------------------------------------
    # 4. Success response + metrics + log (Req 2.2, 7.3, 9.2, 9.3)
    # ------------------------------------------------------------------
    latency_s = time.monotonic() - start_time
    latency_ms = int(latency_s * 1000)

    llm_cache_latency_seconds.labels(operation="write", task_type=task_type).observe(latency_s)

    _log(
        {
            "event": "cache_write",
            "request_id": request_id,
            "cache_key": cache_key,
            "task_type": task_type,
            "latency_ms": latency_ms,
        }
    )

    return WriteResponse(written=True, cache_key=cache_key)
