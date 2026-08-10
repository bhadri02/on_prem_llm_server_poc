"""
admin_portal/routers/ollama_admin.py

Admin-only endpoint to pull a model into the local Ollama instance and
register every model Ollama already has locally into the Model Registry.

Gated by the same session-based `require_admin` dependency (Phase 6 login)
as every other admin-only /portal/* route — pulling a multi-gigabyte model
or registering new inference backends is destructive enough to require
being logged in as an admin, unlike the pre-login-era "no browser-facing
auth" posture the rest of this file's original comment referred to.

Known gap (unchanged by this endpoint): registering a model here does NOT
make it routable. intelligent_router dispatches strictly from the static
model_matrix.yaml loaded once at startup — that file still needs a manual
entry + a Router restart. See docs/FRONTEND_INTEGRATION.md.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends

from admin_portal.config import settings
from admin_portal.schemas.models import OllamaSyncRequest, OllamaSyncResult
from admin_portal.services.session_auth import require_admin

router = APIRouter(tags=["models"])

_client = httpx.AsyncClient()


@router.post(
    "/models/sync-ollama",
    response_model=OllamaSyncResult,
    summary="[admin only] Pull a model into Ollama and register every local Ollama model",
    description=(
        "Requires a logged-in session belonging to a user with the admin "
        "role. If `model` is given, pulls it via Ollama's /api/pull first "
        "(blocking; large models can take minutes — the connection is held "
        "open for the duration). Either way, queries Ollama's /api/tags and "
        "registers any model not already present in the Model Registry "
        "(backend=ollama, status=active). Does NOT update model_matrix.yaml "
        "or restart the Router — newly registered models still need that "
        "manual step before they're actually routable."
    ),
    dependencies=[Depends(require_admin)],
)
async def sync_ollama_models(
    body: OllamaSyncRequest,
) -> OllamaSyncResult:
    ollama_base = settings.OLLAMA_BASE_URL.rstrip("/")

    # --- Optional pull -------------------------------------------------------
    pulled: str | None = None
    failed: dict[str, str] = {}
    if body.model:
        try:
            pull_resp = await _client.post(
                f"{ollama_base}/api/pull",
                json={"name": body.model, "stream": False},
                timeout=settings.OLLAMA_PULL_TIMEOUT_SECONDS,
            )
            if pull_resp.status_code >= 400:
                failed[body.model] = f"ollama pull failed: HTTP {pull_resp.status_code}"
            else:
                pulled = body.model
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_unavailable", "message": f"Ollama unreachable: {exc}", "upstream": "ollama"},
            )

    # --- List everything Ollama has locally -----------------------------------
    try:
        tags_resp = await _client.get(f"{ollama_base}/api/tags", timeout=10.0)
        tags_resp.raise_for_status()
        ollama_models = [m["name"] for m in tags_resp.json().get("models", [])]
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_unavailable", "message": f"Ollama unreachable: {exc}", "upstream": "ollama"},
        )

    # --- Register whatever isn't already in the Model Registry ---------------
    registry_headers = {"X-Api-Key": settings.REGISTRY_API_KEY} if settings.REGISTRY_API_KEY else None
    registered: list[str] = []
    already_registered: list[str] = []

    for name in ollama_models:
        try:
            existing = await _client.get(
                f"{settings.MODEL_REGISTRY_URL}/models/{name}",
                headers=registry_headers,
                timeout=5.0,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            failed[name] = f"model_registry unreachable: {exc}"
            continue

        if existing.status_code == 200:
            already_registered.append(name)
            continue

        create_resp = await _client.post(
            f"{settings.MODEL_REGISTRY_URL}/models/",
            headers=registry_headers,
            timeout=10.0,
            json={
                "name": name,
                "version": "1.0.0",
                "backend": "ollama",
                "endpoint": ollama_base,
                "tasks": body.tasks,
                "status": "active",
            },
        )
        if create_resp.status_code in (200, 201):
            registered.append(name)
        else:
            failed[name] = f"model_registry rejected registration: HTTP {create_resp.status_code}"

    return OllamaSyncResult(
        pulled=pulled,
        ollama_models=ollama_models,
        registered=registered,
        already_registered=already_registered,
        failed=failed,
    )
