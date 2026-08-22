"""
Chat completions router for the API Gateway (Layer 1).

Exposes POST /v1/chat/completions.  Handles both the non-streaming
(stream: false) and streaming (stream: true) paths.

Non-streaming path (task 7.3):
    1. Parse and validate OpenAIChatRequest (FastAPI/Pydantic).
       Any validation error is returned as HTTP 400 via the exported
       ``validation_exception_handler`` (registered on the app in main.py).
    2. build_imf(payload) — normalize to IMF.
    3. Emit ``request_received`` audit event.
    4. forward_to_security(imf, client) — POST IMF to Security Layer.
       On DownstreamError → HTTP 502 + ``response_sent`` audit (outcome="error").
    5. serialize_response(imf_response) → return HTTP 200 JSON.
       Emit ``response_sent`` audit event (outcome="pass").

Streaming path:
    When payload.stream is True, uses forward_to_security_stream(imf, client)
    instead — real token-by-token relay all the way from Ollama/Anthropic
    through inference_adapter -> intelligent_router -> security_layer
    (which applies chunk-level PII re-masking — see
    security_layer/pii.py's StreamingPiiMasker) -> here, translated into
    OpenAI-compatible ``chat.completion.chunk`` SSE events (see
    sse_relay()) ending with ``data: [DONE]``.
    Emits ``response_sent`` audit event when the stream completes or errors.

Validates: Requirements 1.1, 1.5, 1.6, 1.7, 4.1–4.13, 5.1–5.5,
           6.1–6.4, 6.5, 7.1–7.5, 9.1, 9.5
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from api_gateway.config import get_settings
from api_gateway.schemas.audit import AuditEvent
from api_gateway.schemas.openai import OpenAIChatRequest
from api_gateway.services.audit import build_audit_event, emit_audit_event
from api_gateway.services.audit_client import post_audit_event
from api_gateway.services.downstream import DownstreamError, forward_to_security, forward_to_security_stream
from api_gateway.services.normalizer import build_imf
from api_gateway.services.serializer import serialize_response

router = APIRouter()

# ---------------------------------------------------------------------------
# Canonical error response bodies
# ---------------------------------------------------------------------------
_ERROR_400: dict[str, Any] = {"error": {"code": "400", "message": "Bad request"}}
_ERROR_502: dict[str, Any] = {"error": {"code": "502", "message": "Bad gateway"}}


def _dispatch_audit_event(
    background_tasks: BackgroundTasks,
    client: httpx.AsyncClient,
    event: AuditEvent,
) -> None:
    """Emit *event* to stdout immediately and schedule a durable Audit Store
    write as a background task (runs after the response is sent — never
    delays the caller)."""
    emit_audit_event(event)
    settings = get_settings()
    background_tasks.add_task(
        post_audit_event,
        event,
        settings.audit_store_url,
        client,
        settings.audit_api_key,
    )


# ---------------------------------------------------------------------------
# Exception handler for Pydantic / FastAPI request validation errors.
#
# APIRouter does not support exception handlers directly; export this function
# so that main.py can register it on the FastAPI app:
#
#     from api_gateway.routers.chat import validation_exception_handler
#     app.add_exception_handler(RequestValidationError, validation_exception_handler)
# ---------------------------------------------------------------------------
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Convert FastAPI/Pydantic validation errors to HTTP 400.

    FastAPI's default behavior returns HTTP 422 (Unprocessable Entity).
    The spec requires HTTP 400 with the canonical error body instead.
    """
    return JSONResponse(status_code=400, content=_ERROR_400)


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------
async def sse_relay(
    imf,
    client: httpx.AsyncClient,
    request_id: str,
    method: str,
    path: str,
    start_time: float,
    background_tasks: BackgroundTasks,
) -> AsyncGenerator[bytes, None]:
    """Relay security_layer's streaming response as OpenAI-compatible SSE.

    Consumes forward_to_security_stream's ND-JSON chunks
    ({"type": "delta"|"done"|"error", ...}) and reframes each as a
    ``chat.completion.chunk`` SSE event, ending with ``data: [DONE]``.

    The model name in each ``delta`` chunk is a best-effort placeholder
    (``imf.request.model`` — the pinned model, if any, "auto" otherwise)
    since ``routing.selected_model`` isn't known until the Router finishes
    classifying/selecting a model, which only arrives in the final "done"
    event; the final chunk uses the real selected_model once known.

    Dispatches the ``response_sent`` audit event once the stream ends
    (success or error) — mirrors the non-streaming path's audit shape.
    """
    chunk_id = f"chatcmpl-{request_id}"
    created = int(time.time())
    placeholder_model = imf.request.model or "auto"

    def _delta_chunk(content: str, model: str, finish_reason: str | None) -> bytes:
        return (
            "data: "
            + json.dumps(
                {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": finish_reason,
                        }
                    ],
                }
            )
            + "\n\n"
        ).encode()

    outcome = "pass"
    status_code = 200

    try:
        async for chunk in forward_to_security_stream(imf, client):
            chunk_type = chunk.get("type")

            if chunk_type == "delta":
                content = chunk.get("content", "")
                if content:
                    yield _delta_chunk(content, placeholder_model, None)

            elif chunk_type == "error":
                outcome = "error" if chunk.get("status_code", 502) >= 500 else "block"
                status_code = chunk.get("status_code", 502)
                yield (
                    "data: "
                    + json.dumps({"error": {"code": str(status_code), "message": chunk.get("event", "internal_error")}})
                    + "\n\n"
                ).encode()
                break

            elif chunk_type == "done":
                final_imf = chunk.get("imf") or {}
                final_model = (final_imf.get("routing") or {}).get("selected_model") or placeholder_model
                finish_reason = (final_imf.get("response") or {}).get("finish_reason") or "stop"
                yield _delta_chunk("", final_model, finish_reason)
                break

    except DownstreamError as exc:
        outcome = "error"
        status_code = exc.status_code
        yield (
            "data: " + json.dumps({"error": {"code": str(exc.status_code), "message": "Bad gateway"}}) + "\n\n"
        ).encode()

    yield b"data: [DONE]\n\n"

    _dispatch_audit_event(
        background_tasks,
        client,
        build_audit_event(
            request_id=request_id,
            event_type="response_sent",
            method=method,
            path=path,
            status_code=status_code,
            latency_ms=(time.monotonic() - start_time) * 1000,
            outcome=outcome,
        ),
    )


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------
@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse | StreamingResponse:
    """Handle POST /v1/chat/completions.

    Pydantic validation of ``payload`` happens automatically.  Any validation
    error is intercepted by ``validation_exception_handler`` (registered on
    the app in main.py) and returned as HTTP 400.

    Args:
        payload: Validated request body.
        request: Raw Starlette request (used to access ``app.state.http_client``
                 and to resolve the request path/method for audit events).
        background_tasks: FastAPI BackgroundTasks — used to POST each audit
                 event to the Audit Store without delaying the response.

    Returns:
        - Non-streaming: :class:`JSONResponse` (200 on success, 502 on error).
        - Streaming: :class:`StreamingResponse` with
          ``Content-Type: text/event-stream``.
    """
    start_time = time.monotonic()
    method = request.method
    path = request.url.path

    # Retrieve the shared httpx.AsyncClient created by the app lifespan.
    client: httpx.AsyncClient = request.app.state.http_client

    # ------------------------------------------------------------------
    # Step 1 — Normalize payload into IMF
    # ------------------------------------------------------------------
    user_profile = getattr(request.state, "user_profile", None)
    # Reuse the id LoggingMiddleware already generated for this request
    # (request.state.request_id) rather than minting a second, uncorrelated
    # one — AuthMiddleware/RateLimitMiddleware's own audit events for this
    # same request already used it.
    request_id = getattr(request.state, "request_id", None)
    imf = build_imf(payload, user_profile, request_id=request_id)
    request_id = imf.request_id

    # ------------------------------------------------------------------
    # Step 2 — Emit request_received audit event
    # ------------------------------------------------------------------
    _dispatch_audit_event(
        background_tasks,
        client,
        build_audit_event(
            request_id=request_id,
            user_id="poc-user",
            event_type="request_received",
            method=method,
            path=path,
            outcome="pass",
        ),
    )

    # ------------------------------------------------------------------
    # Step 3/4 — Forward to Security Layer.
    #
    # Streaming requests take a completely different downstream call
    # (forward_to_security_stream, real token-by-token relay — see
    # sse_relay()) since nothing about them can be represented as a single
    # request/response round trip; audit dispatch for the streaming path
    # happens inside sse_relay() once the stream ends, not here.
    # ------------------------------------------------------------------
    if payload.stream:
        return StreamingResponse(
            content=sse_relay(imf, client, request_id, method, path, start_time, background_tasks),
            media_type="text/event-stream",
            status_code=200,
            background=background_tasks,
        )

    try:
        imf_response = await forward_to_security(imf, client)
    except DownstreamError as exc:
        # Relay security blocks (400/403) directly; wrap true gateway errors as 502
        if exc.status_code in (400, 403, 429):
            _dispatch_audit_event(
                background_tasks,
                client,
                build_audit_event(
                    request_id=request_id,
                    event_type="response_sent",
                    method=method,
                    path=path,
                    status_code=exc.status_code,
                    latency_ms=(time.monotonic() - start_time) * 1000,
                    outcome="block",
                ),
            )
            return JSONResponse(status_code=exc.status_code, content=exc.body)
        _dispatch_audit_event(
            background_tasks,
            client,
            build_audit_event(
                request_id=request_id,
                event_type="response_sent",
                method=method,
                path=path,
                status_code=502,
                latency_ms=(time.monotonic() - start_time) * 1000,
                outcome="error",
            ),
        )
        return JSONResponse(status_code=502, content=_ERROR_502)

    response_body = serialize_response(imf_response)

    _dispatch_audit_event(
        background_tasks,
        client,
        build_audit_event(
            request_id=request_id,
            user_id="poc-user",
            event_type="response_sent",
            method=method,
            path=path,
            status_code=200,
            latency_ms=(time.monotonic() - start_time) * 1000,
            outcome="pass",
        ),
    )

    return JSONResponse(status_code=200, content=response_body)
