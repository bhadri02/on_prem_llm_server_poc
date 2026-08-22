"""
routers/pre_check.py — POST /security/check handler (and its streaming
counterpart, POST /security/check/stream).

Implements the pre-generation security check endpoint.  Every inbound IMF
passes through the four-stage pre-generation pipeline (injection scan →
content safety → PII masking → policy check), after which the enriched IMF
is forwarded to the downstream Intelligent Router. On a successful Router
response, this handler ALSO runs the post-generation pipeline (PII masking
on response.content — see pipeline.py's run_post_pipeline) before
returning to the caller — see the module-level note below for why that
wasn't happening before.

A pre-audit event is dispatched as a fire-and-forget background task before
any response (blocked or forwarded) is returned to the caller; a second,
post-audit event is dispatched after a successful Router response, mirroring
routers/post_check.py's own audit-event shape.

Note on POST /security/post-check (routers/post_check.py): that endpoint
implements the same run_post_pipeline masking this handler now calls
directly, but nothing in the actual request chain ever called it — api_gateway
only ever calls POST /security/check once per request, and this handler used
to return the Router's raw response straight through with no post-processing
at all, despite CLAUDE.md documenting "security_layer (post-pipeline: PII
mask response.content)" as part of the flow. Folding run_post_pipeline
directly into this handler (and its streaming counterpart) closes that gap
instead of adding a second network hop api_gateway would need to make.
POST /security/post-check itself is left in place (still fully functional,
still tested) in case something external calls it directly, but it's no
longer a load-bearing hop in this service's own request chain.
"""

import datetime
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, StreamingResponse

from security_layer import metrics
from security_layer.audit_client import post_audit_event
from security_layer.config import settings
from security_layer.logging_config import get_logger
from security_layer.models import IMFRequest
from security_layer.pii import StreamingPiiMasker
from security_layer.pipeline import run_pre_pipeline, run_post_pipeline
from security_layer.router_client import (
    RouterInvalidResponseError,
    RouterTimeoutError,
    RouterUnavailableError,
    forward_to_router,
    forward_to_router_stream,
)

logger = get_logger(__name__)

router = APIRouter()

# Entity types that have a dedicated label in the PII counter — mirrors
# routers/post_check.py's own _KNOWN_ENTITY_TYPES.
_KNOWN_ENTITY_TYPES: frozenset[str] = frozenset(settings.pii_entities_list)


def _record_pii_metrics(entity_types: list[str]) -> None:
    for entity in entity_types:
        label = entity if entity in _KNOWN_ENTITY_TYPES else "OTHER"
        metrics.pii_entities_total.labels(entity_type=label).inc()


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


@router.post("/security/check")
async def pre_check(
    body: IMFRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Execute the pre-generation security pipeline and forward to the Router.

    Steps:
        1. Capture handler entry time (``t0``).
        2. Run the four-stage pre-generation pipeline.
        3. Emit a security-decision log entry at INFO level.
        4. Construct the pre-audit event (unconditionally, pass or block).
        5. Dispatch the audit event via background task.
        6. Return blocked HTTP error, Router response, or Router error.

    Args:
        body:             Validated :class:`IMFRequest` from the request body.
        request:          FastAPI :class:`Request` giving access to
                          ``app.state``.
        background_tasks: FastAPI :class:`BackgroundTasks` for fire-and-forget
                          audit dispatching.

    Returns:
        A :class:`JSONResponse` relaying the Router's response on success, an
        appropriate error response on routing failure, or a 400/403 block
        response (body wrapped under ``"detail"`` to match FastAPI's default
        HTTPException shape) when the pre-generation pipeline blocks the
        request.
    """
    # ------------------------------------------------------------------
    # 15.1  Capture handler entry time
    # ------------------------------------------------------------------
    t0 = time.monotonic()

    # Serialise the validated Pydantic model to a plain dict so pipeline
    # stages can mutate it freely.
    imf: dict = body.model_dump()
    request_id: str = imf["request_id"]
    state = request.app.state

    # ------------------------------------------------------------------
    # 15.2  Run pre-generation pipeline
    # ------------------------------------------------------------------
    result = await run_pre_pipeline(imf, state)

    # ------------------------------------------------------------------
    # 15.5  Emit security-decision log entry (after pipeline, before audit)
    # ------------------------------------------------------------------
    outcome_str = "block" if result.blocked else "pass"
    latency_ms = result.latency_ms
    injection_detected: bool = imf["governance"]["injection_score"] == 1.0
    pii_entities_found: list = imf["governance"]["pii_fields_detected"]

    logger.info(
        "security_decision",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "injection_detected": injection_detected,
                "pii_entities_found": pii_entities_found,
                "outcome": outcome_str,
                "latency_ms": latency_ms,
            }
        },
    )

    # ------------------------------------------------------------------
    # 15.2  Construct pre-audit event unconditionally (pass or block)
    # ------------------------------------------------------------------
    user_block = imf.get("user") or {}
    user_id = user_block.get("user_id") if isinstance(user_block, dict) else None

    pre_audit_event: dict = {
        "request_id": request_id,
        "user_id": user_id,
        "layer": "security",
        "event_type": "security_block" if result.blocked else "request_received",
        "outcome": "block" if result.blocked else "pass",
        "error_code": result.block_reason,
        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "latency_ms": result.latency_ms,
        "pii_actions": imf["governance"]["pii_fields_detected"],
        "policy_decisions": imf["governance"]["policy_decisions"],
    }

    # Dispatch audit event BEFORE returning any response.
    background_tasks.add_task(
        post_audit_event,
        pre_audit_event,
        state.settings.audit_store_url,
        state.settings.audit_api_key,
    )

    # ------------------------------------------------------------------
    # 15.3  Blocked response
    # ------------------------------------------------------------------
    # Extract department/model for contract-label metrics (fallback: "unknown")
    _department = imf.get("user", {}).get("department") or "unknown"
    _model = imf.get("routing", {}).get("selected_model") or "unknown"

    if result.blocked:
        metrics.blocks_total.labels(reason=result.block_reason).inc()
        metrics.LAYER_METRICS.record_request(
            status="blocked",
            department=_department,
            model=_model,
            latency_s=time.monotonic() - t0,
        )
        # Return a JSONResponse (not `raise HTTPException`) so that
        # `background_tasks` — already populated with the audit-write task
        # above — actually executes. FastAPI/Starlette only attaches
        # BackgroundTasks to a normally-returned Response; an HTTPException
        # is handled by ExceptionMiddleware's own handler, which builds a
        # fresh Response with no knowledge of this request's BackgroundTasks
        # instance, silently dropping any tasks already added to it. The
        # `{"detail": {...}}` wrapping below reproduces FastAPI's default
        # HTTPException body shape exactly, since callers (api_gateway,
        # tests) depend on that nesting.
        return JSONResponse(
            status_code=result.block_status,
            content={
                "detail": {
                    "error": result.block_reason,
                    "request_id": request_id,
                }
            },
            background=background_tasks,
        )

    # ------------------------------------------------------------------
    # 15.4  Forward to Router
    # ------------------------------------------------------------------
    try:
        status, router_body = await forward_to_router(
            result.imf,
            state.settings.downstream_router_url,
            request_id,
            state.settings.router_timeout_seconds,
        )

        # ------------------------------------------------------------------
        # 15.4b  Post-generation pipeline — PII mask response.content before
        # returning to api_gateway (see module docstring for why this used
        # to be skipped entirely). Only meaningful on a 2xx Router response
        # that actually carries content; error bodies pass through untouched.
        # ------------------------------------------------------------------
        entity_types: list[str] = []
        if status < 300 and isinstance(router_body, dict):
            try:
                router_body, entity_types = await run_post_pipeline(router_body, state)
            except Exception as exc:
                # Graceful degradation, matching post_check.py's own
                # behavior: a Presidio failure must not block the response.
                logger.error(
                    "post_pipeline_presidio_error",
                    extra={"extra_fields": {"request_id": request_id, "error": str(exc)}},
                )

            if entity_types:
                _record_pii_metrics(entity_types)
                background_tasks.add_task(
                    post_audit_event,
                    {
                        "request_id": request_id,
                        "user_id": user_id,
                        "layer": "security",
                        "event_type": "response_sent",
                        "outcome": "pass",
                        "pii_actions": entity_types,
                        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                    },
                    state.settings.audit_store_url,
                    state.settings.audit_api_key,
                )

        metrics.LAYER_METRICS.record_request(
            status="success",
            department=_department,
            model=_model,
            latency_s=time.monotonic() - t0,
        )
        return JSONResponse(status_code=status, content=router_body)

    except RouterTimeoutError:
        metrics.LAYER_METRICS.record_request(
            status="error",
            department=_department,
            model=_model,
            latency_s=time.monotonic() - t0,
        )
        return JSONResponse(
            status_code=504,
            content={"error": "router_timeout", "request_id": request_id},
        )

    except RouterUnavailableError:
        metrics.LAYER_METRICS.record_request(
            status="error",
            department=_department,
            model=_model,
            latency_s=time.monotonic() - t0,
        )
        return JSONResponse(
            status_code=502,
            content={"error": "router_unavailable", "request_id": request_id},
        )

    except RouterInvalidResponseError:
        metrics.LAYER_METRICS.record_request(
            status="error",
            department=_department,
            model=_model,
            latency_s=time.monotonic() - t0,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": "router_invalid_response",
                "request_id": request_id,
            },
        )


# ---------------------------------------------------------------------------
# POST /security/check/stream
# ---------------------------------------------------------------------------


async def _stream_after_pre_check(
    imf: dict,
    state,
    request_id: str,
    t0: float,
    department: str,
    model: str,
    background_tasks: BackgroundTasks,
) -> AsyncIterator[bytes]:
    """Forward to the Router's streaming endpoint and relay its response,
    applying chunk-level PII re-masking to each "delta" (see
    security_layer.pii.StreamingPiiMasker's docstring for the buffering
    strategy and its accepted limitation) instead of masking the complete
    response.content in one shot the way the non-streaming path does.
    """
    masker = StreamingPiiMasker(
        state.analyzer, state.anonymizer, state.settings.pii_enabled, entities=state.settings.pii_entities_list
    )
    masked_so_far: list[str] = []

    try:
        async for chunk in forward_to_router_stream(
            imf, state.settings.downstream_router_url, request_id, state.http_client,
            state.settings.router_timeout_seconds,
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "delta":
                piece = masker.feed(chunk.get("content", ""))
                if piece:
                    masked_so_far.append(piece)
                    yield _ndjson({"type": "delta", "content": piece})

            elif chunk_type == "error":
                tail = masker.finish()
                if tail:
                    masked_so_far.append(tail)
                    yield _ndjson({"type": "delta", "content": tail})
                metrics.LAYER_METRICS.record_request(
                    status="error", department=department, model=model, latency_s=time.monotonic() - t0
                )
                yield _ndjson(chunk)
                return

            elif chunk_type == "done":
                tail = masker.finish()
                if tail:
                    masked_so_far.append(tail)
                    yield _ndjson({"type": "delta", "content": tail})

                result_imf = chunk.get("imf") or {}
                entity_types = masker.entity_types()
                if result_imf.get("response") is not None:
                    result_imf["response"]["content"] = "".join(masked_so_far)
                if entity_types:
                    result_imf.setdefault("governance", {})
                    result_imf["governance"]["pii_masked"] = True
                    existing = result_imf["governance"].get("pii_fields_detected") or []
                    result_imf["governance"]["pii_fields_detected"] = list(
                        dict.fromkeys(list(existing) + entity_types)
                    )
                    _record_pii_metrics(entity_types)

                user_block = result_imf.get("user") or {}
                user_id = user_block.get("user_id") if isinstance(user_block, dict) else None
                # Fire-and-forget, same as everywhere else in this codebase —
                # scheduled via the StreamingResponse's own `background`
                # (runs after the body is fully sent) rather than awaited
                # inline, which would otherwise delay the final "done" chunk
                # reaching the client by however long the audit POST + its
                # own internal retries take.
                background_tasks.add_task(
                    post_audit_event,
                    {
                        "request_id": request_id,
                        "user_id": user_id,
                        "layer": "security",
                        "event_type": "response_sent",
                        "outcome": "pass",
                        "pii_actions": entity_types,
                        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                    },
                    state.settings.audit_store_url,
                    state.settings.audit_api_key,
                )

                metrics.LAYER_METRICS.record_request(
                    status="success", department=department, model=model, latency_s=time.monotonic() - t0
                )
                yield _ndjson({"type": "done", "imf": result_imf})
                return

    except RouterTimeoutError:
        metrics.LAYER_METRICS.record_request(
            status="error", department=department, model=model, latency_s=time.monotonic() - t0
        )
        yield _ndjson({"type": "error", "event": "router_timeout", "status_code": 504, "request_id": request_id})
    except RouterUnavailableError:
        metrics.LAYER_METRICS.record_request(
            status="error", department=department, model=model, latency_s=time.monotonic() - t0
        )
        yield _ndjson({"type": "error", "event": "router_unavailable", "status_code": 502, "request_id": request_id})
    except RouterInvalidResponseError:
        metrics.LAYER_METRICS.record_request(
            status="error", department=department, model=model, latency_s=time.monotonic() - t0
        )
        yield _ndjson({"type": "error", "event": "router_invalid_response", "status_code": 502, "request_id": request_id})


@router.post("/security/check/stream")
async def pre_check_stream(
    body: IMFRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    """Streaming counterpart to POST /security/check.

    Runs the exact same blocking pre-generation pipeline as the
    non-streaming endpoint (injection scan, content safety, request PII
    masking, policy check) — a request that would be blocked is blocked
    identically either way, signaled as a single in-band "error" line
    here instead of an HTTP 400/403 status. Only the successful path
    differs: instead of waiting for the Router's complete response, this
    forwards to the Router's streaming endpoint and relays it with
    chunk-level PII re-masking (see _stream_after_pre_check).
    """
    t0 = time.monotonic()
    imf: dict = body.model_dump()
    request_id: str = imf["request_id"]
    state = request.app.state

    result = await run_pre_pipeline(imf, state)

    outcome_str = "block" if result.blocked else "pass"
    logger.info(
        "security_decision",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "injection_detected": imf["governance"]["injection_score"] == 1.0,
                "pii_entities_found": imf["governance"]["pii_fields_detected"],
                "outcome": outcome_str,
                "latency_ms": result.latency_ms,
            }
        },
    )

    user_block = imf.get("user") or {}
    user_id = user_block.get("user_id") if isinstance(user_block, dict) else None

    background_tasks.add_task(
        post_audit_event,
        {
            "request_id": request_id,
            "user_id": user_id,
            "layer": "security",
            "event_type": "security_block" if result.blocked else "request_received",
            "outcome": "block" if result.blocked else "pass",
            "error_code": result.block_reason,
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "latency_ms": result.latency_ms,
            "pii_actions": imf["governance"]["pii_fields_detected"],
            "policy_decisions": imf["governance"]["policy_decisions"],
        },
        state.settings.audit_store_url,
        state.settings.audit_api_key,
    )

    _department = imf.get("user", {}).get("department") or "unknown"
    _model = imf.get("routing", {}).get("selected_model") or "unknown"

    if result.blocked:
        metrics.blocks_total.labels(reason=result.block_reason).inc()
        metrics.LAYER_METRICS.record_request(
            status="blocked", department=_department, model=_model, latency_s=time.monotonic() - t0
        )

        async def _blocked_stream() -> AsyncIterator[bytes]:
            yield _ndjson({
                "type": "error", "event": result.block_reason,
                "status_code": result.block_status, "request_id": request_id,
            })

        return StreamingResponse(_blocked_stream(), media_type="application/x-ndjson", background=background_tasks)

    return StreamingResponse(
        _stream_after_pre_check(result.imf, state, request_id, t0, _department, _model, background_tasks),
        media_type="application/x-ndjson",
        background=background_tasks,
    )
