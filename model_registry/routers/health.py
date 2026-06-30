"""
Health router for the Model Registry.

Implements GET /health, which returns:
  HTTP 503  {"status": "starting"}                      — during startup
  HTTP 200  {"status": "ok", "storage": "reachable"}    — fully operational
  HTTP 200  {"status": "degraded", "storage": "unreachable"} — storage lost

Exposes a module-level _ready flag that main.py sets to True after storage.load()
completes, bridging the lifespan startup sequence to the health endpoint.

No X-API-Key check is applied on this endpoint (AuthMiddleware only enforces
auth on POST /models and PATCH /models/{name}/status, so /health passes through
freely).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from model_registry.schemas.model import HealthResponse

# ---------------------------------------------------------------------------
# Module-level readiness flag
# ---------------------------------------------------------------------------

# Set to True by main.py lifespan after storage.load() completes.
# Remains False during the entire startup window so that /health correctly
# returns HTTP 503 to Kubernetes readiness probes before the store is loaded.
_ready: bool = False

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["health"])


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health and storage reachability check",
)
async def health_check(request: Request) -> JSONResponse:
    """
    Return the current health status of the Model Registry.

    **Startup not complete** (``_ready == False``):
    - HTTP 503 ``{"status": "starting"}``

    **Startup complete, storage reachable** (``_ready == True``,
    ``storage.storage_reachable() == True``):
    - HTTP 200 ``{"status": "ok", "storage": "reachable"}``

    **Startup complete, storage unreachable** (``_ready == True``,
    ``storage.storage_reachable() == False``):
    - HTTP 200 ``{"status": "degraded", "storage": "unreachable"}``

    No ``X-API-Key`` is required on this endpoint.

    Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
    """
    if not _ready:
        # Req 7.2 — return 503 for the entire startup window
        return JSONResponse(
            status_code=503,
            content={"status": "starting"},
        )

    storage = request.app.state.storage
    if storage.storage_reachable():
        # Req 7.1 — normal operational state
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "storage": "reachable"},
        )

    # Req 7.3 — storage became unreachable after successful startup
    return JSONResponse(
        status_code=200,
        content={"status": "degraded", "storage": "unreachable"},
    )
