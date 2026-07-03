"""
agent_framework/routers/health.py

Health check endpoint for the Agent Framework (Layer 6).

GET /health returns HTTP 200 with {"status": "ok"} without authentication.

Requirements: 1.5, 1.6
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> JSONResponse:
    """Return HTTP 200 with {"status": "ok"} — no authentication required."""
    return JSONResponse(status_code=200, content={"status": "ok"})
