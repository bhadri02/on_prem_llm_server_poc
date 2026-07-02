"""
admin_portal/routers/health.py

Health-check router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /health
    Returns HTTP 200 with ``HealthResponse(status="ok")`` when the service is
    running normally.

    Returns HTTP 503 with ``HealthResponse(status="degraded", reason=<msg>)``
    when a startup failure has been recorded via ``set_startup_failure``.

No authentication is required for this endpoint (Req 1.4).

Startup failure flag
--------------------
``main.py`` calls ``set_startup_failure(reason)`` from the lifespan handler if
config validation fails for a non-fatal reason (e.g. a soft dependency is
absent).  For hard failures (``GATEWAY_API_KEY`` absent) ``config.py`` already
calls ``sys.exit(1)`` before this router is ever used, so the 503 path is for
other degraded-but-alive scenarios.

Validates: Requirements 1.1, 1.3, 1.4
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from admin_portal.schemas.health import HealthResponse

# ---------------------------------------------------------------------------
# Module-level startup failure flag
# ---------------------------------------------------------------------------

_startup_failure_reason: Optional[str] = None


def set_startup_failure(reason: str) -> None:
    """Record a startup failure reason so the health endpoint returns 503."""
    global _startup_failure_reason
    _startup_failure_reason = reason


def clear_startup_failure() -> None:
    """Clear any previously recorded startup failure (used in tests)."""
    global _startup_failure_reason
    _startup_failure_reason = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Portal health check",
    description=(
        "Returns HTTP 200 with status=ok when healthy. "
        "Returns HTTP 503 with status=degraded if a startup failure was recorded."
    ),
)
async def health_check() -> JSONResponse:
    """Return the current health status of the Portal API.

    - HTTP 200: service is running normally.
    - HTTP 503: startup failure was detected; ``reason`` describes the issue.
    """
    if _startup_failure_reason is not None:
        body = HealthResponse(status="degraded", reason=_startup_failure_reason)
        return JSONResponse(status_code=503, content=body.model_dump())

    body = HealthResponse(status="ok")
    return JSONResponse(status_code=200, content=body.model_dump())
