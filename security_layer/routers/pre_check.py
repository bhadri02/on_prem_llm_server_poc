"""
routers/pre_check.py — POST /security/check handler.

Implements the pre-generation security check endpoint.  Every inbound IMF
passes through the four-stage pre-generation pipeline (injection scan →
content safety → PII masking → policy check), after which the enriched IMF
is forwarded to the downstream Intelligent Router.

A pre-audit event is dispatched as a fire-and-forget background task before
any response (blocked or forwarded) is returned to the caller.
"""

import datetime
import time

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from security_layer import metrics
from security_layer.audit_client import post_audit_event
from security_layer.logging_config import get_logger
from security_layer.models import IMFRequest
from security_layer.pipeline import run_pre_pipeline
from security_layer.router_client import (
    RouterInvalidResponseError,
    RouterTimeoutError,
    RouterUnavailableError,
    forward_to_router,
)

logger = get_logger(__name__)

router = APIRouter()


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
