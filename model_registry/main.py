"""
Application factory for the Model Registry FastAPI service.

This module wires together the FastAPI app, lifespan context manager,
middleware (LoggingMiddleware, AuthMiddleware), exception handlers, and routers
(health, models). It also provides a uvicorn entrypoint for local development.

Validates: Requirements 1.2, 7.2, 8.1, 8.2
"""

import json
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from model_registry.config import get_settings
from model_registry.exceptions import DuplicateNameError, ModelNotFoundError, PersistenceError
from model_registry.middleware.auth import AuthMiddleware
from model_registry.middleware.logging import LoggingMiddleware
from model_registry.routers.health import router as health_router
from model_registry.routers.models import router as models_router
from model_registry.storage.json_file_manager import JsonFileManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Async context manager for application lifespan events.

    Startup:
      - Reads settings; fails fast (sys.exit(1)) if REGISTRY_API_KEY is unset,
        unless the operator has explicitly opted into running without auth via
        ALLOW_UNAUTHENTICATED_REGISTRY=true (local/dev convenience only).
      - Emits a structured WARNING to stdout if REGISTRY_ENCRYPTION_KEY is
        unset — provider api_key values will be stored in plaintext.
      - Instantiates JsonFileManager and calls storage.load(); if storage is
        unrecoverable, load() calls sys.exit(1) before yielding.
      - Stores the storage instance on app.state so routers can access it via
        request.app.state.storage.
      - Sets the health router module's _ready flag to True so /health returns
        200 instead of 503 (Req 7.2).

    Shutdown:
      - Resets _ready to False so any in-flight /health probes during shutdown
        return 503 again.
    """
    # --- startup ---
    settings = get_settings()

    if not settings.registry_api_key:
        if settings.allow_unauthenticated_registry:
            print(
                json.dumps({
                    "level": "WARNING",
                    "event": "api_key_not_configured",
                    "message": (
                        "REGISTRY_API_KEY unset; ALLOW_UNAUTHENTICATED_REGISTRY=true "
                        "so auth enforcement is disabled. Do not run this way in production."
                    ),
                }),
                flush=True,
            )
        else:
            print(
                json.dumps({
                    "level": "ERROR",
                    "event": "registry_api_key_required",
                    "message": (
                        "REGISTRY_API_KEY must be set. To explicitly run without "
                        "auth for local/dev use, set ALLOW_UNAUTHENTICATED_REGISTRY=true."
                    ),
                }),
                flush=True,
            )
            sys.exit(1)

    if not settings.registry_encryption_key:
        print(
            json.dumps({
                "level": "WARNING",
                "event": "encryption_key_not_configured",
                "message": (
                    "REGISTRY_ENCRYPTION_KEY unset; provider api_key values will be "
                    "stored in plaintext in models.json."
                ),
            }),
            flush=True,
        )

    storage = JsonFileManager(settings.storage_path, settings.registry_encryption_key or None)
    storage.load()  # may call sys.exit(1) if unrecoverable

    app.state.storage = storage

    # Set the health router's readiness flag via module-level attribute assignment
    import model_registry.routers.health as health_router_module
    health_router_module._ready = True

    yield

    # --- shutdown ---
    health_router_module._ready = False


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(title="Model Registry", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Middleware — outermost first (LoggingMiddleware wraps everything, including auth)
# ---------------------------------------------------------------------------

app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(DuplicateNameError)
async def duplicate_name_handler(request: Request, exc: DuplicateNameError) -> JSONResponse:
    """Return HTTP 409 when a model name already exists in the store."""
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc)},
    )


@app.exception_handler(ModelNotFoundError)
async def model_not_found_handler(request: Request, exc: ModelNotFoundError) -> JSONResponse:
    """Return HTTP 404 when a requested model name is not registered."""
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc)},
    )


@app.exception_handler(PersistenceError)
async def persistence_error_handler(request: Request, exc: PersistenceError) -> JSONResponse:
    """Return HTTP 500 when an atomic write to STORAGE_PATH fails."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(models_router)

# ---------------------------------------------------------------------------
# Development entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("model_registry.main:app", host="0.0.0.0", port=5001)
