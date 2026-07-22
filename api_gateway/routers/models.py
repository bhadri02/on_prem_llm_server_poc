"""
Models router for the API Gateway (Layer 1).

Exposes GET /v1/models, returning a static OpenAI-compatible models list.
Authentication is enforced by AuthMiddleware (X-Api-Key required).

Validates: Requirements 1.2, 1.8
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# POC: static model list — extend or make configurable in Phase 2
STATIC_MODELS: list[str] = ["llama3"]


@router.get("/v1/models")
async def list_models() -> JSONResponse:
    """Return the static list of available models in OpenAI format.

    Authentication is handled upstream by ``AuthMiddleware``; this handler
    will only be reached by requests that have already been validated.

    Returns:
        HTTP 200 with body::

            {
                "object": "list",
                "data": [
                    {"id": "llama3",   "object": "model"},
                    {"id": "mistral",  "object": "model"},
                    {"id": "phi3",     "object": "model"},
                ]
            }
    """
    data = [{"id": model, "object": "model"} for model in STATIC_MODELS]
    return JSONResponse(
        status_code=200,
        content={"object": "list", "data": data},
    )
