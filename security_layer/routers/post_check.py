"""
routers/post_check.py — POST /security/post-check handler.

Implements the post-generation security check endpoint.  Every Router IMF
response passes through PII masking on ``response.content`` before being
returned to the caller.

A post-audit event is dispatched as a fire-and-forget background task before
the response is returned.  If the Presidio engine raises an unhandled
exception, the handler degrades gracefully: it returns HTTP 200 with the
unmasked IMF, sets ``governance.pii_masked`` to ``false``, and still
dispatches the audit event (with ``pii_actions: []``).
"""

import datetime
import time

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from security_layer import metrics
from security_layer.audit_client import post_audit_event
from security_layer.logging_config import get_logger
from security_layer.models import IMFRequest
from security_layer.pipeline import run_post_pipeline
from security_layer.pii import POC_ENTITIES

logger = get_logger(__name__)

router = APIRouter()

# Entity types that have a dedicated label in the PII counter.
_KNOWN_ENTITY_TYPES: frozenset[str] = frozenset(POC_ENTITIES)


@router.post("/security/post-check")
async def post_check(
    body: IMFRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Execute the post-generation PII masking pipeline.

    Steps:
        1. Capture handler entry time (``t0``).
        2. Run the post-generation pipeline (PII masking on response.content).
        3. On unhandled Presidio exception: set ``governance.pii_masked=False``,
           log ERROR, dispatch audit event with ``pii_actions: []``, observe
           latency metric, emit INFO log, return HTTP 200 with unmasked IMF.
        4. Construct the post-audit event and dispatch via background task.
        5. Observe latency metric, increment PII entity counters, emit INFO log.
        6. Return HTTP 200 with enriched IMF.

    Args:
        body:             Validated :class:`IMFRequest` from the request body.
        request:          FastAPI :class:`Request` giving access to
                          ``app.state``.
        background_tasks: FastAPI :class:`BackgroundTasks` for fire-and-forget
                          audit dispatching.

    Returns:
        A :class:`JSONResponse` with the enriched (or unmasked on degradation)
        IMF at HTTP 200.
    """
    # ------------------------------------------------------------------
    # 16.1  Capture handler entry time
    # ------------------------------------------------------------------
    t0 = time.monotonic()

    # Serialise the validated Pydantic model to a plain dict so pipeline
    # stages can mutate it freely.
    imf: dict = body.model_dump()
    request_id: str = imf["request_id"]
    state = request.app.state

    # ------------------------------------------------------------------
    # 16.2  Run post-generation pipeline (graceful degradation on failure)
    # ------------------------------------------------------------------
    try:
        enriched_imf, entity_types = await run_post_pipeline(imf, state)
    except Exception as exc:
        # Graceful degradation per Requirement 2.5: Presidio failure must not
        # block the response.  Return the unmasked IMF with HTTP 200.
        imf["governance"]["pii_masked"] = False
        logger.error(
            "post_pipeline_presidio_error",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "error": str(exc),
                }
            },
        )

        # Audit event still dispatched even on degraded path (pii_actions: []).
        latency_ms = int((time.monotonic() - t0) * 1000)
        user_block = imf.get("user") or {}
        user_id = user_block.get("user_id") if isinstance(user_block, dict) else None

        degraded_audit_event: dict = {
            "request_id": request_id,
            "user_id": user_id,
            "layer": "security",
            "event_type": "response_sent",
            "outcome": "pass",
            "pii_actions": [],
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "latency_ms": latency_ms,
        }
        background_tasks.add_task(
            post_audit_event,
            degraded_audit_event,
            state.settings.audit_store_url,
            state.settings.audit_api_key,
        )

        # Observe latency and emit INFO log on degraded path.
        metrics.LAYER_METRICS.latency_seconds.labels(department="unknown").observe(
            time.monotonic() - t0
        )
        logger.info(
            "post_check_decision",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "pii_entities_found": [],
                    "latency_ms": latency_ms,
                }
            },
        )

        return JSONResponse(status_code=200, content=imf)

    # ------------------------------------------------------------------
    # 16.3  Construct and dispatch post-audit event
    # ------------------------------------------------------------------
    latency_ms = int((time.monotonic() - t0) * 1000)

    user_block = enriched_imf.get("user") or {}
    user_id = user_block.get("user_id") if isinstance(user_block, dict) else None

    audit_event: dict = {
        "request_id": request_id,
        "user_id": user_id,
        "layer": "security",
        "event_type": "response_sent",
        "outcome": "pass",
        "pii_actions": entity_types,
        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "latency_ms": latency_ms,
    }

    # Dispatch BEFORE returning response (fire-and-forget).
    background_tasks.add_task(
        post_audit_event,
        audit_event,
        state.settings.audit_store_url,
        state.settings.audit_api_key,
    )

    # ------------------------------------------------------------------
    # 16.4  Metrics, logging, and response
    # ------------------------------------------------------------------
    # Observe handler latency using contract-label schema.
    _department = enriched_imf.get("user", {}).get("department") or "unknown"
    metrics.LAYER_METRICS.latency_seconds.labels(department=_department).observe(
        time.monotonic() - t0
    )

    # Increment PII entity counter once per detected entity type.
    # Entity types outside the known set use the "OTHER" label.
    for entity in entity_types:
        label = entity if entity in _KNOWN_ENTITY_TYPES else "OTHER"
        metrics.pii_entities_total.labels(entity_type=label).inc()

    # Emit INFO-level post-check log entry (Requirement 12.4).
    logger.info(
        "post_check_decision",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "pii_entities_found": entity_types,
                "latency_ms": latency_ms,
            }
        },
    )

    return JSONResponse(status_code=200, content=enriched_imf)
