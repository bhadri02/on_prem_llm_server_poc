"""
Health router for the Inference Adapter.

Exposes GET /health with a four-state state machine that reflects both the
adapter's startup status and the live reachability of the Ollama backend.

Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from inference_adapter.config import get_settings
from inference_adapter.services.ollama_client import OllamaError

# Set to True by the lifespan startup handler in main.py (Task 10).
# Tests may also set this directly to exercise different states.
_startup_complete: bool = False

_HEALTH_PROBE_TIMEOUT_SECONDS: float = 5.0

health_router = APIRouter()


@health_router.get("/health")
async def health(request: Request) -> JSONResponse:
    """
    Four-state health endpoint for the Inference Adapter.

    State machine (deterministic function of startup state + Ollama check result):

    1. _startup_complete == False
       → HTTP 503 {"status": "starting"}

    2. _startup_complete == True, Ollama /api/tags fails or times out
       → HTTP 503 {"status": "unavailable", "reason": "ollama_unreachable"}

    3. _startup_complete == True, Ollama reachable but DEFAULT_MODEL absent
       → HTTP 503 {"status": "unavailable", "reason": "model_not_loaded", "model": "<model>"}

    4. _startup_complete == True, Ollama reachable and DEFAULT_MODEL present
       → HTTP 200 {"status": "ok", "backend": "ollama", "model": "<model>"}

    The Ollama probe is live on every call (no caching) with a hard 5-second timeout.
    """
    if not _startup_complete:
        return JSONResponse(status_code=503, content={"status": "starting"})

    settings = get_settings()
    ollama_client = request.app.state.ollama_client

    try:
        models: list[str] = await asyncio.wait_for(
            ollama_client.list_models(),
            timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
    except (OllamaError, asyncio.TimeoutError, Exception):
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "ollama_unreachable"},
        )

    default_model = settings.default_model
    if default_model not in models:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "reason": "model_not_loaded",
                "model": default_model,
            },
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ok", "backend": "ollama", "model": default_model},
    )
