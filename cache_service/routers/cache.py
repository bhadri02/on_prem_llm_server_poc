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
    LAYER_METRICS,
    llm_cache_semantic_entries,
)
from cache_service.schemas.cache import CacheBlock, LookupResponse, WriteResponse
from cache_service.schemas.imf import IMFDocument, IMFResponse


# ---------------------------------------------------------------------------
# Cache key derivation helper
# ---------------------------------------------------------------------------

# Some clients' agent-mode harnesses (confirmed: GitHub Copilot Chat's native
# "Bring Your Own Model" integration) append a near-constant tool/context
# reminder block as the LAST message on every single turn, rather than the
# user's own text — e.g. "<context>\nthe current date is ...\n</context>\n
# <reminderinstructions>\nwhen using the insert_edit_into_file tool, ...".
# That block barely changes turn to turn, so treating it as "the current
# turn" (see the docstring below on why *some* last-message assumption is
# needed) makes every real question from this client collide into the same
# cache entry — this was a real, observed bug: "tell me a joke" and "what
# time is it" produced near-identical embeddings (cosine similarity 0.98)
# purely because both prompts' actual last message was this same wrapper.
_HARNESS_WRAPPER_PREFIXES = ("<context>", "<reminderinstructions>")


def _find_current_turn_content(messages: list[dict]) -> str:
    """Return the content of the message that actually represents the
    current turn awaiting a reply.

    Scans backward from the end and returns the first message whose content
    doesn't start with a known harness-wrapper tag, so a trailing reminder
    block doesn't get mistaken for "the current turn". Falls back to the
    true last message if every message looks like a wrapper (keeps existing
    behavior unchanged for clients that don't do this — portal_ui,
    Continue.dev — where the true last message already is the real turn).
    """
    for message in reversed(messages):
        content = (message.get("content") or "").strip()
        if not content.lower().startswith(_HARNESS_WRAPPER_PREFIXES):
            return content
    return (messages[-1].get("content") or "").strip() if messages else ""


def make_cache_key(messages: list[dict], model: str, task_type: str) -> str:
    """Return the SHA-256 hex digest that uniquely identifies a cache entry.

    The key is derived from ONLY the current turn's content (see
    ``_find_current_turn_content()``) — not the whole conversation. The Chat
    UI resends the full accumulated history (including prior assistant
    replies) on every turn since the backend is stateless per-request;
    hashing/embedding all of it would make the cache key (and the
    semantic-cache embedding — see lookup()/write() below) dominated by the
    ever-growing shared prefix of earlier turns rather than the new
    question, causing unrelated questions late in a conversation to collide
    (this was a real bug: "do you know the time" semantically matched a
    cached "good morning" reply purely because both prompts were mostly
    identical multi-turn history with only a small differing tail).

    The key is derived by:
      1. Finding the current turn's content via ``_find_current_turn_content()``
         and stripping whitespace.
      2. Lower-casing it.
      3. Appending ``|{model}|{task_type}`` to form the raw key material.
      4. UTF-8–encoding and SHA-256–hashing the raw string.

    This produces identical keys for any two requests whose current turn
    (after whitespace stripping), model, and task_type are equal —
    regardless of prior conversation history or surrounding whitespace.

    Args:
        messages:  List of message dicts, each containing at minimum a
                   ``"content"`` key (e.g. ``[{"role": "user", "content": "…"}]``).
        model:     The selected model identifier (``routing.selected_model``).
        task_type: The request task type (``request.task_type``).

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    content = _find_current_turn_content(messages).lower()
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
    # Only the current turn — see make_cache_key()'s docstring for why
    # joining the whole conversation history is a real bug, not a style
    # choice, and _find_current_turn_content()'s docstring for why the true
    # last message isn't always the current turn either.
    prompt_text = _find_current_turn_content(messages_as_dicts).lower()

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

        # Extract contract labels from IMF (fallback: "unknown")
        _department = imf.user.department or "unknown" if hasattr(imf, "user") and imf.user else "unknown"
        _model = imf.routing.selected_model or "unknown" if hasattr(imf, "routing") and imf.routing else "unknown"

        # Record via shared LAYER_METRICS (cache layer passes `outcome` kwarg)
        LAYER_METRICS.record_request(
            status="success",
            department=_department,
            model=_model,
            latency_s=latency_s,
            outcome=status,  # "hit" or "miss"
        )

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
            LAYER_METRICS.record_error(
                error_code="redis_unavailable",
                department="unknown",
            )
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
            LAYER_METRICS.record_error(
                error_code="embedding_error",
                department="unknown",
            )
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
            LAYER_METRICS.record_error(
                error_code="redis_unavailable",
                department="unknown",
            )
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
    # Only the current turn — see make_cache_key()'s docstring for why
    # joining the whole conversation history is a real bug, not a style
    # choice, and _find_current_turn_content()'s docstring for why the true
    # last message isn't always the current turn either.
    prompt_text = _find_current_turn_content(messages_as_dicts).lower()

    # Uniform TTL across every task_type (Req: cache hits only within 1
    # minute, then a miss, regardless of format/task).
    ttl: int = settings.cache_ttl_seconds

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
            _department = imf.user.department or "unknown" if hasattr(imf, "user") and imf.user else "unknown"
            LAYER_METRICS.record_error(
                error_code="redis_unavailable",
                department=_department,
            )
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
            _department = imf.user.department or "unknown" if hasattr(imf, "user") and imf.user else "unknown"
            LAYER_METRICS.record_error(
                error_code="embedding_error",
                department=_department,
            )
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
            _department = imf.user.department or "unknown" if hasattr(imf, "user") and imf.user else "unknown"
            LAYER_METRICS.record_error(
                error_code="redis_unavailable",
                department=_department,
            )
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

    _department = imf.user.department or "unknown" if hasattr(imf, "user") and imf.user else "unknown"
    _model = imf.routing.selected_model or "unknown" if hasattr(imf, "routing") and imf.routing else "unknown"

    # Record successful write using contract-label schema.
    # For the write operation, outcome="miss" is used since it wasn't a cache hit.
    LAYER_METRICS.record_request(
        status="success",
        department=_department,
        model=_model,
        latency_s=latency_s,
        outcome="miss",
    )

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
