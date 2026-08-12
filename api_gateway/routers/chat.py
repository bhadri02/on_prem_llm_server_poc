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

Streaming path (task 7.4):
    Uses the exact same forward_to_security(imf, client) call as the
    non-streaming path — nothing downstream (Ollama/Anthropic) produces
    incremental tokens, the full response always comes back in one piece.
    When payload.stream is True, that one completed response is instead
    framed as a single OpenAI ``chat.completion.chunk`` SSE event followed
    by ``data: [DONE]`` (see sse_single_chunk()), rather than returned as a
    plain JSON body.
    Emit ``response_sent`` audit event when the stream completes or errors.

Validates: Requirements 1.1, 1.5, 1.6, 1.7, 4.1–4.13, 5.1–5.5,
           6.1–6.4, 6.5, 7.1–7.5, 9.1, 9.5
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from api_gateway.schemas.openai import OpenAIChatRequest
from api_gateway.services.audit import build_audit_event, emit_audit_event
from api_gateway.services.downstream import DownstreamError, forward_to_security
from api_gateway.services.normalizer import build_imf
from api_gateway.services.serializer import serialize_response

router = APIRouter()

# ---------------------------------------------------------------------------
# Canonical error response bodies
# ---------------------------------------------------------------------------
_ERROR_400: dict[str, Any] = {"error": {"code": "400", "message": "Bad request"}}
_ERROR_502: dict[str, Any] = {"error": {"code": "502", "message": "Bad gateway"}}


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
async def sse_single_chunk(response_body: dict) -> AsyncGenerator[bytes, None]:
    """Emit a completed OpenAI chat-completion dict as one SSE stream.

    Nothing in this pipeline generates tokens incrementally — Ollama and
    Anthropic responses are both received in full before ``forward_to_security``
    returns — so there is no real per-token data to stream. This sends the
    complete response as a single ``chat.completion.chunk`` event (in the
    ``choices[].delta`` shape SSE clients expect) followed by the ``[DONE]``
    sentinel, which is enough for OpenAI-compatible SSE clients (e.g.
    Continue.dev) to render the full answer at once rather than
    token-by-token.

    Args:
        response_body: The dict produced by ``serialize_response()``.

    Yields:
        Two byte chunks: the ``data: {...}`` event, then ``data: [DONE]``.
    """
    choice = response_body["choices"][0]
    chunk = {
        "id": response_body["id"],
        "object": "chat.completion.chunk",
        "created": response_body["created"],
        "model": response_body["model"],
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": choice["message"]["content"],
                },
                "finish_reason": choice["finish_reason"],
            }
        ],
    }
    yield f"data: {json.dumps(chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------
@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
) -> JSONResponse | StreamingResponse:
    """Handle POST /v1/chat/completions.

    Pydantic validation of ``payload`` happens automatically.  Any validation
    error is intercepted by ``validation_exception_handler`` (registered on
    the app in main.py) and returned as HTTP 400.

    Args:
        payload: Validated request body.
        request: Raw Starlette request (used to access ``app.state.http_client``
                 and to resolve the request path/method for audit events).

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
    imf = build_imf(payload, user_profile)
    request_id = imf.request_id

    # Propagate request_id to request state so middleware can correlate logs
    request.state.request_id = request_id

    # ------------------------------------------------------------------
    # Step 2 — Emit request_received audit event
    # ------------------------------------------------------------------
    emit_audit_event(
        build_audit_event(
            request_id=request_id,
            user_id="poc-user",
            event_type="request_received",
            method=method,
            path=path,
            outcome="pass",
        )
    )

    # ------------------------------------------------------------------
    # Step 3/4 — Forward to Security Layer (same call for both streaming
    # and non-streaming requests — nothing downstream produces incremental
    # tokens, so "streaming" only changes how the one completed result is
    # framed on the way back out; see sse_single_chunk()).
    # ------------------------------------------------------------------
    try:
        imf_response = await forward_to_security(imf, client)
    except DownstreamError as exc:
        # Relay security blocks (400/403) directly; wrap true gateway errors as 502
        if exc.status_code in (400, 403, 429):
            emit_audit_event(
                build_audit_event(
                    request_id=request_id,
                    event_type="response_sent",
                    method=method,
                    path=path,
                    status_code=exc.status_code,
                    latency_ms=(time.monotonic() - start_time) * 1000,
                    outcome="block",
                )
            )
            return JSONResponse(status_code=exc.status_code, content=exc.body)
        emit_audit_event(
            build_audit_event(
                request_id=request_id,
                event_type="response_sent",
                method=method,
                path=path,
                status_code=502,
                latency_ms=(time.monotonic() - start_time) * 1000,
                outcome="error",
            )
        )
        return JSONResponse(status_code=502, content=_ERROR_502)

    response_body = serialize_response(imf_response)

    emit_audit_event(
        build_audit_event(
            request_id=request_id,
            user_id="poc-user",
            event_type="response_sent",
            method=method,
            path=path,
            status_code=200,
            latency_ms=(time.monotonic() - start_time) * 1000,
            outcome="pass",
        )
    )

    if payload.stream:
        return StreamingResponse(
            content=sse_single_chunk(response_body),
            media_type="text/event-stream",
            status_code=200,
        )

    return JSONResponse(status_code=200, content=response_body)
