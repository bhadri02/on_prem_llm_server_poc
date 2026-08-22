"""
Infer router for the Inference Adapter.

Exposes POST /infer — validates the incoming IMF document, dispatches to
Ollama or a cloud provider (based on routing.backend, stamped by the
Router — see intelligent_router/pipeline.py Stage 3), maps the response
back to IMF, and updates Prometheus metrics before returning.

Error mapping (Ollama path — routing.backend == "ollama" or absent):
  missing routing.selected_model          → 422  (pydantic / custom)
  missing / empty request.messages        → 422  event=empty_messages
  selected_model not in loaded model list → 422  event=model_not_loaded
  Ollama timeout / connection error       → 503  event=ollama_unreachable
  Ollama HTTP 4xx                         → 422  event=ollama_request_rejected
  Ollama HTTP 5xx                         → 502  event=ollama_backend_error
  Ollama response not valid JSON          → 502  event=ollama_invalid_response
  Ollama response missing message/content → 502  event=ollama_invalid_response

Error mapping (cloud path — routing.backend == "anthropic"):
  Model Registry unreachable              → 503  event=model_registry_unreachable
  no api_key on file for this model       → 422  event=provider_api_key_not_configured
  provider timeout / connection error     → 503  event=anthropic_unreachable
  provider HTTP 4xx                       → 422  event=anthropic_request_rejected
  provider HTTP 5xx                       → 502  event=anthropic_backend_error
  provider response unparseable           → 502  event=anthropic_invalid_response
  unrecognised routing.backend value      → 422  event=unsupported_backend

  Unhandled Python exception              → 500  event=internal_error

Validates: Requirements 1.1, 1.6, 1.7, 1.8, 1.9, 1.10,
           9.1, 9.2, 9.3, 9.4, 9.5, 9.6,
           10.1, 10.2, 10.3,
           11.2, 11.3, 11.4, 11.5,
           14.1, 14.2, 14.3, 14.4
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from inference_adapter.config import get_settings
from inference_adapter.metrics import LAYER_METRICS
from inference_adapter.schemas.imf import IMFDocument, IMFResponse, IMFUsage
from inference_adapter.services.imf_mapper import IMFMapper
from inference_adapter.services.anthropic_client import (
    AnthropicBackendError,
    AnthropicClient,
    AnthropicConnectionError,
    AnthropicInvalidResponseError,
    AnthropicRequestError,
    AnthropicTimeoutError,
)
from inference_adapter.services.model_secret_resolver import (
    ModelSecretUnavailable,
    resolve_api_key,
)
from inference_adapter.services.ollama_client import (
    OllamaBackendError,
    OllamaConnectionError,
    OllamaInvalidResponseError,
    OllamaRequestError,
    OllamaTimeoutError,
)

infer_router = APIRouter()

# ---------------------------------------------------------------------------
# Streaming wire protocol (internal, service-to-service — NOT the
# browser-facing SSE format; api_gateway is the only hop that translates to
# that). Newline-delimited JSON, one object per line:
#
#   {"type": "delta", "content": "<text>"}          — zero or more
#   {"type": "done", "imf": {...full IMFDocument}}  — exactly one, last, on success
#   {"type": "error", "event": "<code>", "status_code": <int>, ...}
#                                                     — exactly one, in place
#                                                       of "done", on failure
#
# HTTP status is always 200 for /infer/stream itself — failures are signaled
# in-band via the "error" line so a partial stream (error after some deltas
# already flushed) can still be represented; callers must inspect the first
# line before assuming success.
# ---------------------------------------------------------------------------


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log(entry: dict) -> None:
    """Write a JSON-serialised log entry to stdout. Never raises."""
    try:
        sys.stdout.write(json.dumps(entry) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format with a trailing 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Cloud-backend dispatch (routing.backend != "ollama")
# ---------------------------------------------------------------------------


async def _dispatch_cloud_backend(
    imf: IMFDocument,
    request: Request,
    request_id: str | None,
    backend: str,
    model_label: str,
    department_label: str,
) -> JSONResponse:
    """Handle POST /infer for a model whose routing.backend != "ollama".

    Currently the only recognised cloud backend is "anthropic"; anything
    else returns 422 unsupported_backend. Mirrors the Ollama dispatch
    block's structure (log → dispatch → map errors → success), using
    parallel `anthropic_*` event names so the two paths never share an
    ambiguous error code.
    """
    settings = get_settings()

    if backend != "anthropic":
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=0.0
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "unsupported_backend",
                "backend": backend,
                "request_id": request_id,
            },
        )

    # ---- Resolve the provider API key from the Model Registry ------------
    try:
        api_key = await resolve_api_key(model_label, settings, request.app.state.http_client)
    except ModelSecretUnavailable as exc:
        _log({"event": "model_registry_unreachable", "request_id": request_id, "detail": str(exc)})
        LAYER_METRICS.record_error(error_code="model_registry_unreachable", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=0.0
        )
        return JSONResponse(
            status_code=503,
            content={"event": "model_registry_unreachable", "request_id": request_id},
        )

    if not api_key:
        LAYER_METRICS.record_error(error_code="provider_api_key_not_configured", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=0.0
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "provider_api_key_not_configured",
                "model": model_label,
                "request_id": request_id,
            },
        )

    _log(
        {
            "event": "inference_start",
            "request_id": request_id,
            "model": model_label,
            "backend": backend,
            "timestamp_utc": _utc_now_iso(),
        }
    )

    anthropic_client = request.app.state.anthropic_client
    start_ns = time.monotonic_ns()

    try:
        anthropic_payload = IMFMapper.to_anthropic_request(imf, settings)
        anthropic_resp = await anthropic_client.messages(anthropic_payload, api_key)
        imf_out = IMFMapper.to_imf_response_from_anthropic(
            imf,
            anthropic_resp,
            wall_clock_ms=(time.monotonic_ns() - start_ns) // 1_000_000,
        )

    except (AnthropicTimeoutError, AnthropicConnectionError):
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log({"event": "inference_error", "request_id": request_id, "model": model_label,
              "error_code": "anthropic_unreachable", "latency_ms": latency_ms})
        LAYER_METRICS.record_error(error_code="anthropic_unreachable", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
        )
        return JSONResponse(status_code=503, content={"event": "anthropic_unreachable", "request_id": request_id})

    except AnthropicRequestError:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log({"event": "inference_error", "request_id": request_id, "model": model_label,
              "error_code": "anthropic_request_rejected", "latency_ms": latency_ms})
        LAYER_METRICS.record_error(error_code="anthropic_error_response", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
        )
        return JSONResponse(status_code=422, content={"event": "anthropic_request_rejected", "request_id": request_id})

    except AnthropicBackendError:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log({"event": "inference_error", "request_id": request_id, "model": model_label,
              "error_code": "anthropic_backend_error", "latency_ms": latency_ms})
        LAYER_METRICS.record_error(error_code="anthropic_error_response", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
        )
        return JSONResponse(status_code=502, content={"event": "anthropic_backend_error", "request_id": request_id})

    except AnthropicInvalidResponseError:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log({"event": "inference_error", "request_id": request_id, "model": model_label,
              "error_code": "anthropic_invalid_response", "latency_ms": latency_ms})
        LAYER_METRICS.record_error(error_code="anthropic_unparseable_body", department=department_label)
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
        )
        return JSONResponse(status_code=502, content={"event": "anthropic_invalid_response", "request_id": request_id})

    except Exception:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
        )
        return JSONResponse(status_code=500, content={"event": "internal_error", "request_id": request_id})

    # ---- Success -----------------------------------------------------
    latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
    usage = imf_out.response.usage if imf_out.response else None
    _log(
        {
            "event": "inference_complete",
            "request_id": request_id,
            "model": model_label,
            "backend": backend,
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
            "latency_ms": latency_ms,
        }
    )
    LAYER_METRICS.record_request(
        status="success", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
    )
    return JSONResponse(status_code=200, content=imf_out.model_dump())


# ---------------------------------------------------------------------------
# Streaming dispatch (routing.backend == "ollama")
# ---------------------------------------------------------------------------


async def _stream_ollama(
    imf: IMFDocument,
    request: Request,
    request_id: str | None,
    model_label: str,
    department_label: str,
) -> AsyncIterator[bytes]:
    """Stream a chat completion from Ollama, relaying deltas as they arrive.

    Mirrors the non-streaming Ollama dispatch block's error mapping (see
    module docstring) but signals failures as an in-band "error" line
    instead of an HTTP status code, since the response has already started
    streaming with a 200 by the time most failures could occur.
    """
    settings = get_settings()
    ollama_client = request.app.state.ollama_client
    accumulated: list[str] = []
    start_ns = time.monotonic_ns()

    _log({"event": "inference_start", "request_id": request_id, "model": model_label,
          "timestamp_utc": _utc_now_iso()})

    try:
        ollama_payload = IMFMapper.to_ollama_request(imf, settings)
        async for chunk in ollama_client.chat_stream(ollama_payload):
            message = chunk.get("message") or {}
            piece = message.get("content") or ""
            if piece:
                accumulated.append(piece)
                yield _ndjson({"type": "delta", "content": piece})

            if chunk.get("done"):
                finish_reason = IMFMapper.resolve_finish_reason(chunk.get("done_reason"))
                prompt_tokens, completion_tokens, total_tokens = IMFMapper.resolve_token_counts(
                    chunk.get("prompt_eval_count"), chunk.get("eval_count")
                )
                wall_clock_ms = (time.monotonic_ns() - start_ns) // 1_000_000
                total_duration = chunk.get("total_duration", 0)
                inference_latency_ms = (
                    math.floor(total_duration / 1_000_000) if total_duration > 0 else wall_clock_ms
                )
                imf_out = imf.model_copy(update={
                    "response": IMFResponse(
                        content="".join(accumulated),
                        finish_reason=finish_reason,
                        usage=IMFUsage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                        ),
                    ),
                    "metadata": {
                        "inference_backend": "ollama",
                        "inference_latency_ms": inference_latency_ms,
                        "model_name": imf.routing.selected_model,
                    },
                    "extensions": {},
                })
                _log({"event": "inference_complete", "request_id": request_id, "model": model_label,
                      "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": total_tokens, "latency_ms": wall_clock_ms})
                LAYER_METRICS.record_request(
                    status="success", department=department_label, model=model_label,
                    latency_s=wall_clock_ms / 1000.0,
                )
                yield _ndjson({"type": "done", "imf": imf_out.model_dump()})
                return

    except (OllamaTimeoutError, OllamaConnectionError):
        event = "ollama_unreachable"
        status_code = 503
    except OllamaRequestError:
        event = "ollama_request_rejected"
        status_code = 422
    except OllamaBackendError:
        event = "ollama_backend_error"
        status_code = 502
    except OllamaInvalidResponseError:
        event = "ollama_invalid_response"
        status_code = 502
    except Exception:
        event = "internal_error"
        status_code = 500
    else:
        # Stream ended without a "done" chunk — treat as an invalid response.
        event = "ollama_invalid_response"
        status_code = 502

    latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
    _log({"event": "inference_error", "request_id": request_id, "model": model_label,
          "error_code": event, "latency_ms": latency_ms})
    LAYER_METRICS.record_error(error_code=event, department=department_label)
    LAYER_METRICS.record_request(
        status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
    )
    yield _ndjson({"type": "error", "event": event, "status_code": status_code, "request_id": request_id})


# ---------------------------------------------------------------------------
# Streaming dispatch (routing.backend == "anthropic")
# ---------------------------------------------------------------------------


async def _stream_anthropic(
    imf: IMFDocument,
    request: Request,
    request_id: str | None,
    model_label: str,
    department_label: str,
) -> AsyncIterator[bytes]:
    """Stream a chat completion from Anthropic, relaying deltas as they arrive.

    Mirrors _dispatch_cloud_backend's error mapping (see module docstring)
    but signals failures as an in-band "error" line — see _stream_ollama's
    docstring for why.
    """
    settings = get_settings()

    try:
        api_key = await resolve_api_key(model_label, settings, request.app.state.http_client)
    except ModelSecretUnavailable:
        yield _ndjson({"type": "error", "event": "model_registry_unreachable", "status_code": 503, "request_id": request_id})
        return

    if not api_key:
        yield _ndjson({"type": "error", "event": "provider_api_key_not_configured", "status_code": 422,
                       "model": model_label, "request_id": request_id})
        return

    _log({"event": "inference_start", "request_id": request_id, "model": model_label,
          "backend": "anthropic", "timestamp_utc": _utc_now_iso()})

    anthropic_client = request.app.state.anthropic_client
    accumulated: list[str] = []
    start_ns = time.monotonic_ns()

    try:
        anthropic_payload = IMFMapper.to_anthropic_request(imf, settings)
        async for chunk in anthropic_client.messages_stream(anthropic_payload, api_key):
            if chunk.get("type") == "content_delta":
                text = chunk.get("text") or ""
                if text:
                    accumulated.append(text)
                    yield _ndjson({"type": "delta", "content": text})
            elif chunk.get("type") == "done":
                wall_clock_ms = (time.monotonic_ns() - start_ns) // 1_000_000
                finish_reason = IMFMapper.resolve_anthropic_finish_reason(chunk.get("stop_reason"))
                usage = chunk.get("usage") or {}
                prompt_tokens = usage.get("input_tokens") or 0
                completion_tokens = usage.get("output_tokens") or 0
                imf_out = imf.model_copy(update={
                    "response": IMFResponse(
                        content="".join(accumulated),
                        finish_reason=finish_reason,
                        usage=IMFUsage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens,
                        ),
                    ),
                    "metadata": {
                        "inference_backend": "anthropic",
                        "inference_latency_ms": wall_clock_ms,
                        "model_name": imf.routing.selected_model,
                    },
                    "extensions": {},
                })
                _log({"event": "inference_complete", "request_id": request_id, "model": model_label,
                      "backend": "anthropic", "prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens, "latency_ms": wall_clock_ms})
                LAYER_METRICS.record_request(
                    status="success", department=department_label, model=model_label,
                    latency_s=wall_clock_ms / 1000.0,
                )
                yield _ndjson({"type": "done", "imf": imf_out.model_dump()})
                return

    except (AnthropicTimeoutError, AnthropicConnectionError):
        event = "anthropic_unreachable"
        status_code = 503
    except AnthropicRequestError:
        event = "anthropic_request_rejected"
        status_code = 422
    except AnthropicBackendError:
        event = "anthropic_backend_error"
        status_code = 502
    except AnthropicInvalidResponseError:
        event = "anthropic_invalid_response"
        status_code = 502
    except Exception:
        event = "internal_error"
        status_code = 500
    else:
        event = "anthropic_invalid_response"
        status_code = 502

    latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
    _log({"event": "inference_error", "request_id": request_id, "model": model_label,
          "error_code": event, "latency_ms": latency_ms})
    LAYER_METRICS.record_error(error_code=event, department=department_label)
    LAYER_METRICS.record_request(
        status="error", department=department_label, model=model_label, latency_s=latency_ms / 1000.0
    )
    yield _ndjson({"type": "error", "event": event, "status_code": status_code, "request_id": request_id})


# ---------------------------------------------------------------------------
# POST /infer/stream
# ---------------------------------------------------------------------------


@infer_router.post("/infer/stream")
async def infer_stream(imf: IMFDocument, request: Request) -> StreamingResponse:
    """Streaming counterpart to POST /infer — see module docstring's wire
    protocol section. Runs the exact same pre-flight validation as /infer
    (missing selected_model / empty messages / model not loaded) before
    dispatching, emitting the same event codes as the first (and only)
    line of the stream instead of as the HTTP status.
    """
    request_id: str | None = imf.request_id
    model_label: str = imf.routing.selected_model or ""
    department_label: str = imf.user.department or ""

    async def _single_error(event: str, status_code: int, **extra) -> AsyncIterator[bytes]:
        LAYER_METRICS.record_request(
            status="error", department=department_label, model=model_label, latency_s=0.0
        )
        yield _ndjson({"type": "error", "event": event, "status_code": status_code,
                       "request_id": request_id, **extra})

    if not imf.routing.selected_model:
        return StreamingResponse(
            _single_error("missing_selected_model", 422), media_type="application/x-ndjson"
        )

    model_label = imf.routing.selected_model

    if not imf.request.messages:
        return StreamingResponse(
            _single_error("empty_messages", 422), media_type="application/x-ndjson"
        )

    backend = (imf.routing.backend or "ollama").lower()
    if backend != "ollama":
        if backend != "anthropic":
            return StreamingResponse(
                _single_error("unsupported_backend", 422, backend=backend),
                media_type="application/x-ndjson",
            )
        return StreamingResponse(
            _stream_anthropic(imf, request, request_id, model_label, department_label),
            media_type="application/x-ndjson",
        )

    ollama_models: list[str] = request.app.state.ollama_models
    if imf.routing.selected_model not in ollama_models:
        return StreamingResponse(
            _single_error("model_not_loaded", 422, model=imf.routing.selected_model),
            media_type="application/x-ndjson",
        )

    return StreamingResponse(
        _stream_ollama(imf, request, request_id, model_label, department_label),
        media_type="application/x-ndjson",
    )


# ---------------------------------------------------------------------------
# POST /infer
# ---------------------------------------------------------------------------


@infer_router.post("/infer")
async def infer(imf: IMFDocument, request: Request) -> JSONResponse:
    """
    Main inference endpoint.

    Accepts a fully populated IMF envelope, validates it, dispatches to the
    Ollama backend, maps the response back to IMF, and returns HTTP 200 with
    the updated envelope.

    Prometheus counters and histograms are updated before every return path.
    """
    request_id: str | None = imf.request_id

    # ------------------------------------------------------------------
    # Extract Prometheus label values up-front (safe defaults for errors)
    # ------------------------------------------------------------------
    model_label: str = imf.routing.selected_model or ""
    task_type_label: str = imf.request.task_type or ""
    department_label: str = imf.user.department or ""

    # ------------------------------------------------------------------
    # Validation: routing.selected_model must be present and non-null
    # ------------------------------------------------------------------
    if not imf.routing.selected_model:
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=0.0,
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "missing_selected_model",
                "request_id": request_id,
            },
        )

    # Re-set model_label now that we know it's non-null
    model_label = imf.routing.selected_model

    # ------------------------------------------------------------------
    # Validation: request.messages must be present and non-empty
    # ------------------------------------------------------------------
    if not imf.request.messages:
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=0.0,
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "empty_messages",
                "request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # Backend branch — routing.backend is stamped by the Router
    # (intelligent_router/pipeline.py Stage 3). Absent/None means "ollama"
    # for backward compatibility with callers that don't set it (including
    # every pre-existing test fixture). Cloud backends skip the
    # ollama_models membership check entirely — that list only makes sense
    # for models actually loaded into this adapter's local Ollama instance.
    # ------------------------------------------------------------------
    backend = (imf.routing.backend or "ollama").lower()
    if backend != "ollama":
        return await _dispatch_cloud_backend(
            imf, request, request_id, backend, model_label, department_label
        )

    # ------------------------------------------------------------------
    # Validation: selected_model must be in the loaded model list
    # ------------------------------------------------------------------
    ollama_models: list[str] = request.app.state.ollama_models
    if imf.routing.selected_model not in ollama_models:
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=0.0,
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "model_not_loaded",
                "model": imf.routing.selected_model,
                "request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # Streaming guard — log warning, continue with stream=False
    # ------------------------------------------------------------------
    if imf.request.stream:
        _log(
            {
                "event": "streaming_not_supported",
                "request_id": request_id,
            }
        )
        # Proceed non-streaming (OllamaClient always forces stream=False)

    # ------------------------------------------------------------------
    # Emit inference_start log
    # ------------------------------------------------------------------
    _log(
        {
            "event": "inference_start",
            "request_id": request_id,
            "model": model_label,
            "timestamp_utc": _utc_now_iso(),
        }
    )

    # ------------------------------------------------------------------
    # Dispatch to Ollama
    # ------------------------------------------------------------------
    settings = get_settings()
    ollama_client = request.app.state.ollama_client

    start_ns = time.monotonic_ns()

    try:
        ollama_payload = IMFMapper.to_ollama_request(imf, settings)
        ollama_resp = await ollama_client.chat(ollama_payload)
        imf_out = IMFMapper.to_imf_response(
            imf,
            ollama_resp,
            wall_clock_ms=(time.monotonic_ns() - start_ns) // 1_000_000,
        )

    # ---- Ollama unreachable (timeout or connection failure) -----------
    except (OllamaTimeoutError, OllamaConnectionError) as exc:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log(
            {
                "event": "inference_error",
                "request_id": request_id,
                "model": model_label,
                "error_code": "ollama_unreachable",
                "latency_ms": latency_ms,
            }
        )
        LAYER_METRICS.record_error(
            error_code="ollama_unreachable",
            department=department_label,
        )
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=latency_ms / 1000.0,
        )
        return JSONResponse(
            status_code=503,
            content={
                "event": "ollama_unreachable",
                "request_id": request_id,
            },
        )

    # ---- Ollama HTTP 4xx ---------------------------------------------
    except OllamaRequestError as exc:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log(
            {
                "event": "inference_error",
                "request_id": request_id,
                "model": model_label,
                "error_code": "ollama_request_rejected",
                "latency_ms": latency_ms,
            }
        )
        LAYER_METRICS.record_error(
            error_code="ollama_error_response",
            department=department_label,
        )
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=latency_ms / 1000.0,
        )
        return JSONResponse(
            status_code=422,
            content={
                "event": "ollama_request_rejected",
                "request_id": request_id,
            },
        )

    # ---- Ollama HTTP 5xx ---------------------------------------------
    except OllamaBackendError as exc:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log(
            {
                "event": "inference_error",
                "request_id": request_id,
                "model": model_label,
                "error_code": "ollama_backend_error",
                "latency_ms": latency_ms,
            }
        )
        LAYER_METRICS.record_error(
            error_code="ollama_error_response",
            department=department_label,
        )
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=latency_ms / 1000.0,
        )
        return JSONResponse(
            status_code=502,
            content={
                "event": "ollama_backend_error",
                "request_id": request_id,
            },
        )

    # ---- Ollama response unparseable (JSON or missing fields) --------
    except OllamaInvalidResponseError as exc:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        _log(
            {
                "event": "inference_error",
                "request_id": request_id,
                "model": model_label,
                "error_code": "ollama_invalid_response",
                "latency_ms": latency_ms,
            }
        )
        LAYER_METRICS.record_error(
            error_code="ollama_unparseable_body",
            department=department_label,
        )
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=latency_ms / 1000.0,
        )
        return JSONResponse(
            status_code=502,
            content={
                "event": "ollama_invalid_response",
                "request_id": request_id,
            },
        )

    # ---- Unhandled exception -----------------------------------------
    except Exception:
        latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        LAYER_METRICS.record_request(
            status="error",
            department=department_label,
            model=model_label,
            latency_s=latency_ms / 1000.0,
        )
        return JSONResponse(
            status_code=500,
            content={
                "event": "internal_error",
                "request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------
    latency_ms = (time.monotonic_ns() - start_ns) // 1_000_000

    # Extract token counts from the populated response for the success log
    usage = imf_out.response.usage if imf_out.response else None
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    total_tokens = usage.total_tokens if usage else 0

    _log(
        {
            "event": "inference_complete",
            "request_id": request_id,
            "model": model_label,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
        }
    )

    # Update Prometheus metrics (success)
    LAYER_METRICS.record_request(
        status="success",
        department=department_label,
        model=model_label,
        latency_s=latency_ms / 1000.0,
    )

    return JSONResponse(
        status_code=200,
        content=imf_out.model_dump(),
    )
