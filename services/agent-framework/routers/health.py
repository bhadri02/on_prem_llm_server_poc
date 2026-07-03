"""
routers/health.py — Health check endpoint (stub).

Full implementation is delivered in Task 9.
This stub is importable without errors and registers the router.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> JSONResponse:
    """Return HTTP 200 with {"status": "ok"} — no authentication required."""
    return JSONResponse(status_code=200, content={"status": "ok"})
