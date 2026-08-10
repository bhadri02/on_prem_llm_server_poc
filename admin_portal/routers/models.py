"""
admin_portal/routers/models.py

Models proxy router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /portal/models
    Returns the full model list from the Model Registry.
    Proxies to ``{MODEL_REGISTRY_URL}/`` with a 5-second timeout and returns
    the upstream body and status code unchanged.

    On upstream connectivity failure or timeout, returns HTTP 502 with an
    ``ErrorResponse(error="upstream_unavailable", upstream="model-registry")``.

PATCH /portal/models/{name}/status
    Updates the lifecycle status of a named model.

    Accepts a JSON body ``{"status": "<value>"}`` where ``<value>`` must be
    one of ``"active"``, ``"retired"``, or ``"staging"``.  If the provided
    value is not in the allowed set, returns HTTP 422 with an ``ErrorResponse``
    that includes ``allowed_values: ["active", "retired", "staging"]``.

    Forwards the validated body to
    ``{MODEL_REGISTRY_URL}/models/{name}/status`` and propagates the upstream
    response (status code + body) unchanged.

    If the Model Registry returns HTTP 404, the endpoint returns HTTP 404 with
    ``ErrorResponse(error="not_found", message="Model '{name}' not found.")``.

    On upstream connectivity failure or timeout, returns HTTP 502 with an
    ``ErrorResponse(error="upstream_unavailable", upstream="model-registry")``.

Metrics
-------
- ``llm_portal_requests_total``   incremented on every call (success or error).
- ``llm_portal_latency_seconds``  records proxy round-trip latency.
- ``llm_portal_errors_total``     incremented with the relevant ``error_code``
  on 422 (validation_error), 404 (not_found), and 502 (upstream_unavailable).

JSON request/response logging is handled by the middleware — nothing extra here.

Validates: Requirements 6.4, 7.1, 7.2, 7.3, 7.5, 7.6, 7.7
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from admin_portal.config import settings
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.schemas.models import ModelApiKeyPatch, ModelRegisterRequest, ModelStatusPatch
from admin_portal.services.proxy import ProxyUnavailableError, async_proxy
from admin_portal.services.session_auth import get_current_session, require_admin

# ---------------------------------------------------------------------------
# Module-level HTTP client — reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_client = httpx.AsyncClient()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT_LIST = "/portal/models"
_ENDPOINT_STATUS = "/portal/models/{name}/status"
_PROXY_TIMEOUT = 5.0  # seconds (Req 6.4, 7.7)

_ALLOWED_STATUSES = ["active", "retired", "staging"]

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(tags=["models"], dependencies=[Depends(get_current_session)])


# ---------------------------------------------------------------------------
# GET /portal/models
# ---------------------------------------------------------------------------

@router.get(
    "/models",
    summary="List registered models",
    description=(
        "Return the full model list from the Model Registry unchanged.  "
        "Returns HTTP 502 with ``upstream='model-registry'`` if the registry "
        "is unreachable or times out."
    ),
)
async def list_models() -> Response:
    """Proxy a GET request for the model list to the Model Registry.

    - Forwards to ``{MODEL_REGISTRY_URL}/`` with a 5-second timeout.
    - Propagates the upstream status code and body back to the caller unchanged.
    - Returns HTTP 502 on upstream network failure / timeout.
    """
    upstream_url = f"{settings.MODEL_REGISTRY_URL}/models/"
    t_start = time.monotonic()

    # --- Req 6.4, 7.7: Proxy and handle unavailable upstream ----------------
    try:
        upstream_response = await async_proxy(
            _client,
            "GET",
            upstream_url,
            timeout=_PROXY_TIMEOUT,
            headers={"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_LIST).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_LIST, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_LIST, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_LIST).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT_LIST,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# PATCH /portal/models/{name}/status
# ---------------------------------------------------------------------------

@router.patch(
    "/models/{name}/status",
    summary="Update model status",
    description=(
        "Update the lifecycle status of a named model.  ``status`` must be "
        "one of ``active``, ``retired``, or ``staging``.  Returns HTTP 422 "
        "with ``allowed_values`` if the value is invalid.  Returns HTTP 404 "
        "if the model does not exist.  Returns HTTP 502 on upstream failure."
    ),
    dependencies=[Depends(require_admin)],
)
async def update_model_status(name: str, request: Request) -> Response:
    """Proxy a status PATCH request to the Model Registry.

    Uses a raw ``Request`` body to allow custom 422 handling with
    ``allowed_values``.  The body is manually validated against
    ``ModelStatusPatch`` so that validation failures produce an
    ``ErrorResponse`` envelope rather than FastAPI's default 422 shape.

    - Validates ``status`` ∈ ``{active, retired, staging}``; HTTP 422 on failure.
    - Forwards to ``{MODEL_REGISTRY_URL}/models/{name}/status``.
    - Propagates upstream status code + body unchanged.
    - Returns HTTP 404 with an ``ErrorResponse`` identifying ``name`` when the
      registry returns 404.
    - Returns HTTP 502 on upstream network failure / timeout.
    """
    endpoint = _ENDPOINT_STATUS.format(name=name)
    t_start = time.monotonic()

    # --- Req 7.5: Manual validation with custom 422 envelope ----------------
    try:
        raw_body: Dict[str, Any] = await request.json()
    except Exception:
        raw_body = {}

    # Validate using Pydantic but catch the error to enrich the 422 response.
    try:
        patch = ModelStatusPatch(**raw_body)
    except Exception:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
        llm_portal_requests_total.labels(endpoint=endpoint, status="4xx").inc()
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="validation_error"
        ).inc()
        error_body = ErrorResponse(
            error="validation_error",
            message=(
                f"'status' must be one of {_ALLOWED_STATUSES}; "
                f"got {raw_body.get('status')!r}."
            ),
            allowed_values=_ALLOWED_STATUSES,
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=422,
            media_type="application/json",
        )

    upstream_url = f"{settings.MODEL_REGISTRY_URL}/models/{name}/status"

    # --- Req 7.3, 7.6, 7.7: Proxy and handle 404 / unavailable upstream ----
    try:
        upstream_response = await async_proxy(
            _client,
            "PATCH",
            upstream_url,
            json=patch.model_dump(),
            timeout=_PROXY_TIMEOUT,
            headers={"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
        llm_portal_requests_total.labels(endpoint=endpoint, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=endpoint,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    # --- Req 7.6: Translate registry 404 into a descriptive ErrorResponse ---
    if upstream_response.status_code == 404:
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="not_found"
        ).inc()
        error_body = ErrorResponse(
            error="not_found",
            message=f"Model '{name}' not found.",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=404,
            media_type="application/json",
        )

    # Propagate all other upstream responses unchanged (Req 7.3)
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# POST /portal/models  — register a new model
# ---------------------------------------------------------------------------

_ENDPOINT_REGISTER = "/portal/models"


@router.post(
    "/models",
    summary="Register a new model",
    description=(
        "Register a new model in the Model Registry. api_key is required "
        "for cloud backends (e.g. backend='anthropic') and stored server-side "
        "— it is never echoed back in any response. Returns HTTP 409 if a "
        "model with the same name already exists."
    ),
    dependencies=[Depends(require_admin)],
)
async def register_model(body: ModelRegisterRequest) -> Response:
    upstream_url = f"{settings.MODEL_REGISTRY_URL}/models/"
    t_start = time.monotonic()

    try:
        upstream_response = await async_proxy(
            _client,
            "POST",
            upstream_url,
            json=body.model_dump(),
            timeout=_PROXY_TIMEOUT,
            headers={"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_REGISTER).observe(latency)
        llm_portal_requests_total.labels(endpoint=_ENDPOINT_REGISTER, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=_ENDPOINT_REGISTER, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT_REGISTER).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=_ENDPOINT_REGISTER,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# PATCH /portal/models/{name}/api-key  — set/update a cloud model's provider key
# ---------------------------------------------------------------------------

_ENDPOINT_API_KEY = "/portal/models/{name}/api-key"


@router.patch(
    "/models/{name}/api-key",
    summary="Set or update a model's provider API key",
    description=(
        "Set or update the provider API key the Inference Adapter uses to "
        "dispatch to this model (cloud backends only, e.g. Anthropic). The "
        "raw key is never returned — the response only confirms "
        "api_key_set=true. Returns HTTP 404 if the model doesn't exist."
    ),
    dependencies=[Depends(require_admin)],
)
async def update_model_api_key(name: str, body: ModelApiKeyPatch) -> Response:
    endpoint = _ENDPOINT_API_KEY.format(name=name)
    t_start = time.monotonic()

    try:
        upstream_response = await async_proxy(
            _client,
            "PATCH",
            f"{settings.MODEL_REGISTRY_URL}/models/{name}/api-key",
            json=body.model_dump(),
            timeout=_PROXY_TIMEOUT,
            headers={"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None,
        )
    except ProxyUnavailableError:
        latency = time.monotonic() - t_start
        llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
        llm_portal_requests_total.labels(endpoint=endpoint, status="5xx").inc()
        llm_portal_errors_total.labels(
            endpoint=endpoint, error_code="upstream_unavailable"
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
    llm_portal_latency_seconds.labels(endpoint=endpoint).observe(latency)
    llm_portal_requests_total.labels(
        endpoint=endpoint,
        status=get_status_class(upstream_response.status_code),
    ).inc()

    if upstream_response.status_code == 404:
        llm_portal_errors_total.labels(endpoint=endpoint, error_code="not_found").inc()
        error_body = ErrorResponse(
            error="not_found",
            message=f"Model '{name}' not found.",
        )
        return Response(
            content=error_body.model_dump_json(),
            status_code=404,
            media_type="application/json",
        )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )
