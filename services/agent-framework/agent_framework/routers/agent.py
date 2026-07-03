"""
agent_framework/routers/agent.py

POST /agent/run endpoint for the Agent Framework (Layer 6).

Validates extensions.agentic, delegates to run_agent_session(), and handles
global exception with HTTP 500 fallback containing a partial IMF.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.8, 13.5
"""

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from agent_framework import metrics
from agent_framework.agent.session_store import session_store
from agent_framework.audit import emit_audit_event
from agent_framework.logging_config import get_logger
from agent_framework.schemas.imf import IMFDocument

logger = get_logger(__name__)
router = APIRouter()


@router.post("/agent/run")
async def agent_run(
    body: IMFDocument,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Accept an IMF JSON body, validate extensions.agentic=true, and
    delegate to the agent orchestrator.

    - HTTP 400 if extensions.agentic is absent or false.
    - HTTP 200 with populated IMF on success.
    - HTTP 502 if the Router sub-call fails.
    - HTTP 500 with partial IMF + finish_reason=null on unhandled error.
    """
    t0 = time.monotonic()
    imf = body.model_dump()
    request_id = imf.get("request_id", "unknown")

    # Validate the agentic flag (Req 1.3)
    if not imf.get("extensions", {}).get("agentic"):
        metrics.errors_total.labels(error_code="400").inc()
        return JSONResponse(
            status_code=400,
            content={
                "error": "validation_error",
                "field": "extensions.agentic",
                "message": "extensions.agentic must be true to invoke the agent",
                "request_id": request_id,
            },
        )

    try:
        # Lazy import to avoid pulling in langchain/langgraph at module load time,
        # which allows the router module to be imported in test environments where
        # those heavy ML dependencies may not be installed.
        from agent_framework.agent.orchestrator import run_agent_session  # noqa: PLC0415

        output_imf, status_code = await run_agent_session(
            imf=imf,
            tool_registry=request.app.state.tool_registry,
            session_store=session_store,
        )
        if status_code >= 400:
            metrics.errors_total.labels(error_code=str(status_code)).inc()

        # Emit completion audit event as a background task so the response
        # is not blocked (fire-and-forget per design).
        background_tasks.add_task(
            emit_audit_event,
            {
                "audit_id": str(uuid.uuid4()),
                "request_id": request_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "layer": "agent",
                "event_type": "response_sent",
                "status_code": status_code,
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "outcome": "pass" if status_code < 400 else "error",
            },
        )

        return JSONResponse(status_code=status_code, content=output_imf)

    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)

        logger.error(
            "unhandled_exception",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "latency_ms": latency_ms,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

        metrics.errors_total.labels(error_code="500").inc()

        # Build a partial IMF with finish_reason=null (Req 1.8)
        partial_imf = dict(imf)
        partial_imf["response"] = {
            "content": f"Internal server error: {type(exc).__name__}",
            "finish_reason": None,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
        partial_imf.setdefault("metadata", {})

        return JSONResponse(
            status_code=500,
            content=partial_imf,
        )
