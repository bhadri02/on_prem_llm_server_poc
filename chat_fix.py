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
    When payload.stream is True, open an httpx streaming connection to the
    Security Layer and proxy the SSE byte stream back to the client via
    FastAPI StreamingResponse (Content-Type: text/event-stream).
    Emit ``response_sent`` audit event when the stream completes or errors.

Validates: Requirements 1.1, 1.5, 1.6, 1.7, 4.1–4.13, 5.1–5.5,
           6.1–6.4, 6.5, 7.1–7.5, 9.1, 9.5
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from api_gateway.config import get_settings
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
async def stream_generator(
    resp: httpx.Response,
    request_id: str,
    method: str,
    path: str,
    start_time: float,
) -> AsyncGenerator[bytes, None]:
    """Proxy SSE bytes from the downstream response to the client.

    Yields each raw byte chunk received from the Security Layer directly
    to the client without accumulating the full body.  Emits a
    ``response_sent`` audit event when the stream concludes (pass or error).

    Args:
        resp:       The open :class:`httpx.Response` from the streaming call.
        request_id: The ``request_id`` for audit correlation.
        method:     HTTP method of the original request.
        path:       URL path of the original request.
        start_time: ``time.monotonic()`` timestamp recorded before the request
                    was processed, used to compute ``latency_ms``.

    Yields:
        Raw bytes chunks as received from downstream.
    """
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
        # Stream completed successfully
        emit_audit_event(
            build_audit_event(
                request_id=request_id,
                event_type="response_sent",
                method=method,
                path=path,
                status_code=200,
                latency_ms=(time.monotonic() - start_time) * 1000,
                outcome="pass",
            )
        )
    except Exception:
        # Downstream error mid-stream — close connection and audit
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
        return


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
    imf = build_imf(payload)
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
    # Step 3 — Streaming path
    # ------------------------------------------------------------------
    if payload.stream:
        settings = get_settings()
        url = f"{settings.downstream_security_url}/security/check"

        try:
            # Open the streaming connection; kept alive for the generator.
            # The context manager is entered here; the response object (and
            # the underlying connection) stay open while stream_generator
            # is consuming bytes.
            async with client.stream(
                "POST",
                url,
                json=imf.model_dump(),
                headers={"Content-Type": "application/json"},
                timeout=settings.downstream_timeout_seconds,
            ) as resp:
                if resp.status_code != 200:
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

                return StreamingResponse(
                    content=stream_generator(resp, request_id, method, path, start_time),
                    media_type="text/event-stream",
                    status_code=200,
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError):
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

    # ------------------------------------------------------------------
    # Step 4 — Non-streaming path
    # ------------------------------------------------------------------
    try:
        imf_response = await forward_to_security(imf, client)
    except DownstreamError:
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

    return JSONResponse(status_code=200, content=response_body)
