"""
intelligent_router/routers/openai_compat.py

POST /v1/chat/completions — OpenAI-compatible chat completions endpoint.

Accepts an OpenAI-format chat request, translates it into an Internal Message
Format (IMF) dict, runs it through the six-stage routing pipeline, and returns
an OpenAI-compatible response.

No X-API-Key authentication is required on this endpoint (POC default).

Requirements: 2.8, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from intelligent_router.models import OpenAIChatRequest
from intelligent_router.pipeline import run_routing_pipeline

openai_router = APIRouter()


@openai_router.post("/v1/chat/completions")
async def post_chat_completions(
    body: OpenAIChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Handle POST /v1/chat/completions — OpenAI-compatible chat endpoint.

    Translates the OpenAI request body into an IMF dict, runs the routing
    pipeline, and maps the result back to an OpenAI-compatible response shape.

    Args:
        body:             Validated OpenAIChatRequest Pydantic model.
        request:          FastAPI Request (used to access app.state).
        background_tasks: FastAPI BackgroundTasks for fire-and-forget work.

    Returns:
        JSONResponse with an OpenAI-compatible chat completion on success, or
        a structured OpenAI-compatible error body on failure.
    """
    # ------------------------------------------------------------------
    # Req 9.3 — Belt-and-suspenders: explicit empty messages check.
    # Pydantic's min_length=1 on messages will reject an empty list before
    # reaching here, but the spec requires an explicit handler-level guard.
    # ------------------------------------------------------------------
    if not body.messages:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": 422,
                    "message": "messages array is required and must be non-empty",
                }
            },
        )

    # ------------------------------------------------------------------
    # Determine routing mode and request_id
    # ------------------------------------------------------------------
    request_id = str(uuid.uuid4())
    routing_mode = "pinned" if (body.model and body.model.strip()) else "auto"

    # ------------------------------------------------------------------
    # Construct the complete IMF dict (Req 9.1, 9.2)
    # ------------------------------------------------------------------
    imf: dict = {
        "request_id": request_id,
        "trace_id": None,
        "span_id": None,
        "timestamp_utc": (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        ),
        "user": {
            "user_id": "poc-user",
            "department": "poc",
            "roles": ["developer"],
            "auth_method": "api_key",
        },
        "request": {
            "messages": [m.model_dump() for m in body.messages],
            "model": body.model,
            "task_type": None,
            "stream": body.stream,
            "max_tokens": body.max_tokens,
            "temperature": body.temperature,
        },
        "governance": {
            "pii_masked": False,
            "pii_fields_detected": [],
            "injection_score": 0.0,
            "jailbreak_score": 0.0,
            "content_safety_passed": True,
            "human_approval_required": False,
            "human_approval_status": "not_required",
            "policy_decisions": [],
        },
        "routing": {
            "selected_model": None,
            "routing_mode": routing_mode,
            "fallback_level": 0,
        },
        "cache": {
            "lookup_hit": False,
            "cache_key": None,
        },
        "response": {
            "content": None,
            "finish_reason": None,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        },
        "metadata": {},
        "extensions": {},
    }

    # ------------------------------------------------------------------
    # Run the routing pipeline (Req 9.4)
    # ------------------------------------------------------------------
    result = await run_routing_pipeline(imf, request.app.state, background_tasks)

    # ------------------------------------------------------------------
    # Error path — Req 9.6
    # ------------------------------------------------------------------
    if not result.success:
        return JSONResponse(
            status_code=result.status_code,
            content={
                "error": {
                    "code": result.status_code,
                    "message": result.error_code,
                    "type": "service_unavailable",
                }
            },
        )

    # ------------------------------------------------------------------
    # Success path — build OpenAI-compatible response (Req 9.2, 9.5)
    # ------------------------------------------------------------------
    result_imf = result.imf
    response_block = result_imf.get("response") or {}
    usage_block = response_block.get("usage") or {}

    # Ensure all usage values are non-negative integers
    prompt_tokens = max(0, int(usage_block.get("prompt_tokens") or 0))
    completion_tokens = max(0, int(usage_block.get("completion_tokens") or 0))
    total_tokens = max(0, int(usage_block.get("total_tokens") or 0))

    openai_response = {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": (result_imf.get("routing") or {}).get("selected_model"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_block.get("content"),
                },
                "finish_reason": response_block.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }

    return JSONResponse(status_code=200, content=openai_response)
