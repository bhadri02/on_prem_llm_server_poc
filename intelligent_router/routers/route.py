"""
intelligent_router/routers/route.py

POST /route — primary IMF endpoint for the Intelligent Router (Layer 3).

Accepts a fully-formed IMF from the Security & Governance Layer, runs it
through the six-stage routing pipeline, and returns the completed IMF or a
structured error body.

Success outcomes:
  cache_hit          — Stage 4 returned a valid cached response
  fallback_success   — Inference succeeded after one or more fallbacks
  inference_success  — Inference succeeded on the first attempt

Error outcomes (with matching HTTP status codes):
  governance_check_failed  — 400
  invalid_pinned_model     — 422
  all_backends_exhausted   — 503
  internal_error           — 500
"""

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from intelligent_router import metrics
from intelligent_router.logging_config import get_logger
from intelligent_router.models import IMFRequest
from intelligent_router.pipeline import run_routing_pipeline

logger = get_logger(__name__)

route_router = APIRouter()

# Recognised error codes that are tracked in metrics.errors_total.
_TRACKED_ERROR_CODES = frozenset(
    {
        "governance_check_failed",
        "all_backends_exhausted",
        "invalid_pinned_model",
        "internal_error",
    }
)


@route_router.post("/route")
async def post_route(
    body: IMFRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Handle POST /route — run the routing pipeline and return the completed IMF.

    Args:
        body:             Validated IMFRequest Pydantic model from the caller.
        request:          FastAPI Request (used to access app.state).
        background_tasks: FastAPI BackgroundTasks for fire-and-forget work.

    Returns:
        JSONResponse with the completed IMF on success, or a structured error
        body with the appropriate HTTP status code on failure.
    """
    # Convert the validated Pydantic model to a plain dict so that the
    # pipeline can mutate it freely without coupling to Pydantic internals.
    imf: dict = body.model_dump()

    result = await run_routing_pipeline(imf, request.app.state, background_tasks)

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------
    if result.success:
        # Determine outcome label from IMF routing/cache state.
        if result.imf.get("cache", {}).get("lookup_hit"):
            outcome = "cache_hit"
        elif (result.imf.get("routing", {}).get("fallback_level") or 0) > 0:
            outcome = "fallback_success"
        else:
            outcome = "inference_success"

        task_type = result.imf.get("request", {}).get("task_type", "unknown")
        routing_mode = result.imf.get("routing", {}).get("routing_mode", "unknown")
        department = result.imf.get("user", {}).get("department") or "unknown"
        model = result.imf.get("routing", {}).get("selected_model") or "unknown"

        # Record request using contract-label schema (Req 2.19, 2.20)
        metrics.LAYER_METRICS.record_request(
            status="success",
            department=department,
            model=model,
            latency_s=result.latency_ms / 1000,
        )

        # Track cache hits and fallbacks via extra router metrics
        if outcome == "cache_hit":
            metrics.cache_hits_total.labels(task_type=task_type, model=model).inc()
        elif outcome == "fallback_success":
            metrics.fallbacks_total.labels(task_type=task_type, reason="fallback").inc()

        # Structured routing_decision log entry (Req 13.2)
        logger.info(
            "routing_decision",
            extra={
                "extra_fields": {
                    "request_id": result.imf.get("request_id"),
                    "task_type": task_type,
                    "selected_model": result.imf.get("routing", {}).get("selected_model"),
                    "routing_mode": routing_mode,
                    "cache_hit": result.imf.get("cache", {}).get("lookup_hit", False),
                    "fallback_level": result.imf.get("routing", {}).get("fallback_level", 0),
                    "outcome": outcome,
                    "latency_ms": result.latency_ms,
                }
            },
        )

        return JSONResponse(status_code=200, content=result.imf)

    # ------------------------------------------------------------------
    # Error path
    # ------------------------------------------------------------------
    error_code = result.error_code or "internal_error"

    # Increment errors counter only for recognised error codes
    if error_code in _TRACKED_ERROR_CODES:
        department = result.imf.get("user", {}).get("department") or "unknown"
        metrics.LAYER_METRICS.record_error(
            error_code=error_code,
            department=department,
        )
        metrics.LAYER_METRICS.record_request(
            status="error",
            department=department,
            model=result.imf.get("routing", {}).get("selected_model") or "unknown",
            latency_s=result.latency_ms / 1000,
        )

    # Build the base error body
    error_body: dict = {
        "error": error_code,
        "request_id": result.imf.get("request_id"),
    }

    # Append extra context fields for specific error codes
    if error_code == "all_backends_exhausted":
        error_body["fallback_level"] = result.imf.get("routing", {}).get("fallback_level")

    elif error_code == "invalid_pinned_model":
        error_body["model"] = result.imf.get("request", {}).get("model")

    return JSONResponse(status_code=result.status_code, content=error_body)
