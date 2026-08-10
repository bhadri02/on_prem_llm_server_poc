"""
admin_portal/routers/chat.py

Chat proxy endpoints for the Portal UI's Chat view (Phase 4 — non-streaming,
per the MVP scope decision: no end-to-end SSE streaming exists yet in the
core pipeline, so this mirrors the existing Playground request/response
model rather than adding a `/completions/stream` endpoint).

POST /chat/completions
    Forwards {model, messages, temperature} to the API Gateway using the
    portal's own resolved API key (GATEWAY_API_KEY) — structurally the same
    proxy as routers/playground.py::playground_chat. Kept as a distinct
    endpoint from /playground/chat per the plan's intentional Chat vs
    Playground split.

GET /chat/models
    Returns every active model, each annotated with `entitled: bool` per
    GATEWAY_API_KEY's model_entitlements (empty entitlements = entitled to
    all). Non-entitled models are included, not hidden — Section 3.2 of the
    plan wants them shown greyed-out/locked in the UI, not omitted. Looks
    up the DB row directly — Admin Portal already owns this DB (Phase 1) —
    no need for a self-HTTP-call to its own /keys/resolve.
"""

from __future__ import annotations

import json
import time

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin_portal.config import settings
from admin_portal.db.keys import hash_key
from admin_portal.db.models import ApiKey, KeyModelEntitlement
from admin_portal.db.session import get_db
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.schemas.playground import ChatRequest
from admin_portal.services.proxy import ProxyUnavailableError, async_proxy
from admin_portal.services.session_auth import AuthContext, get_current_session

# ---------------------------------------------------------------------------
# Module-level HTTP client — reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_client = httpx.AsyncClient()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT_COMPLETIONS = "/portal/chat/completions"
_ENDPOINT_MODELS = "/portal/chat/models"
_PROXY_TIMEOUT = 30.0  # seconds

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# POST /chat/completions
# ---------------------------------------------------------------------------


@router.post(
    "/chat/completions",
    summary="Chat completion",
    description=(
        "Forward a chat request to the API Gateway using the portal's own "
        "API key. Returns HTTP 502 if the API Gateway is unreachable."
    ),
)
async def chat_completions(body: ChatRequest, ctx: AuthContext = Depends(get_current_session)) -> Response:
    upstream_url = f"{settings.API_GATEWAY_URL}/v1/chat/completions"
    # Uses the LOGGED-IN user's own session-scoped key (Phase 6), not the
    # portal's own fixed GATEWAY_API_KEY — this is what makes per-user RBAC
    # (policy_denied / model_not_entitled) actually reflect who's asking.
    headers = {"X-API-Key": ctx.api_key_raw}

    t_start = time.monotonic()
    try:
        upstream_response = await async_proxy(
            _client,
            "POST",
            upstream_url,
            headers=headers,
            json=body.model_dump(),
            timeout=_PROXY_TIMEOUT,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_COMPLETIONS).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_COMPLETIONS, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_COMPLETIONS, error_code="upstream_unavailable"
        ).inc()
        error_body = ErrorResponse(
            error="upstream_unavailable",
            message="The API Gateway is unreachable or timed out.",
            upstream="api-gateway",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=502,
            media_type="application/json",
        )

    latency = time.monotonic() - t_start
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_COMPLETIONS).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT_COMPLETIONS,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# GET /chat/models
# ---------------------------------------------------------------------------


@router.get(
    "/chat/models",
    summary="List models available to the Chat view",
    description=(
        "Returns the active model list filtered by the portal's own API "
        "key's model_entitlements (empty entitlements = all active models)."
    ),
)
async def chat_models(
    db: Session = Depends(get_db), ctx: AuthContext = Depends(get_current_session)
) -> Response:
    t_start = time.monotonic()

    digest = hash_key(ctx.api_key_raw)
    api_key = db.execute(select(ApiKey).where(ApiKey.key_hash == digest)).scalar_one_or_none()
    entitlements: list[str] = []
    if api_key is not None:
        entitlements = [
            model_name
            for (model_name,) in db.execute(
                select(KeyModelEntitlement.model_name).where(
                    KeyModelEntitlement.key_id == api_key.key_id
                )
            ).all()
        ]

    upstream_url = f"{settings.MODEL_REGISTRY_URL}/models/"
    try:
        upstream_response = await async_proxy(
            _client,
            "GET",
            upstream_url,
            timeout=5.0,
            headers={"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_MODELS).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_MODELS, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_MODELS, error_code="upstream_unavailable"
        ).inc()
        error_body = ErrorResponse(
            error="upstream_unavailable",
            message="The Model Registry is unreachable or timed out.",
            upstream="model-registry",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=502,
            media_type="application/json",
        )

    latency = time.monotonic() - t_start
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_MODELS).observe(latency)

    if upstream_response.status_code != 200:
        llm_portal_requests_total.labels(
            endpoint=_ENDPOINT_MODELS,
            status=get_status_class(upstream_response.status_code),
        ).inc()
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "application/json"),
        )

    try:
        all_models: list[dict] = upstream_response.json()
    except Exception:
        all_models = []

    # Every active model is returned — not just entitled ones — annotated
    # with `entitled`, so the Chat view can show locked/greyed-out models
    # (per the mockup's "Your entitled models" list, which displays models
    # the caller can't use yet rather than hiding them outright). Empty
    # entitlements means "all models" (the backward-compat rule from
    # Phase 2), so every active model is marked entitled in that case.
    active = [m for m in all_models if m.get("status") == "active"]
    annotated = [
        {**m, "entitled": (not entitlements or m.get("name") in entitlements)}
        for m in active
    ]

    llm_portal_requests_total.labels(endpoint=_ENDPOINT_MODELS, status="2xx").inc()

    return Response(
        content=json.dumps(annotated),
        status_code=200,
        media_type="application/json",
    )
