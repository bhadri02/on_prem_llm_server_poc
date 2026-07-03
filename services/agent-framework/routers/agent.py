"""
routers/agent.py — POST /agent/run endpoint (stub).

Full implementation is delivered in Task 9.
This stub is importable without errors and registers the router.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/agent/run")
async def agent_run() -> JSONResponse:
    """Placeholder — full implementation in Task 9."""
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "message": "agent/run not yet implemented"},
    )
