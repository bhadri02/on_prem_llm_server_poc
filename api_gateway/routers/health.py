"""
Health router for the API Gateway (Layer 1).

Exposes GET /health as a liveness probe. Auth and rate-limit middleware
both exempt this path, so no X-Api-Key header is required.

Validates: Requirements 1.3
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> JSONResponse:
    """Return a simple liveness response.

    Returns:
        HTTP 200 with body ``{"status": "ok"}``.
    """
    return JSONResponse(status_code=200, content={"status": "ok"})
