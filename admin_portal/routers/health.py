"""
admin_portal/routers/health.py

Health-check router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /health
    Pure liveness probe — returns HTTP 200 with ``HealthResponse(status="ok")``
    whenever the process is up, regardless of whether its dependencies
    (Postgres) are reachable. Never touches the database, so it can't be
    slowed down or falsely marked unhealthy by a Postgres blip.

    Returns HTTP 503 with ``HealthResponse(status="degraded", reason=<msg>)``
    when a startup failure has been recorded via ``set_startup_failure``.

GET /ready
    Readiness probe — additionally runs ``SELECT 1`` against Postgres via the
    same ``get_db`` dependency every other DB-backed router uses (so tests
    can override it exactly like they do elsewhere), returning HTTP 503 with
    ``status="not_ready"`` if the database is unreachable. Use this (not
    ``/health``) for anything that should actually gate on "can this service
    do its job" — e.g. docker-compose's healthcheck / ``depends_on:
    condition: service_healthy`` for services that call admin_portal.

No authentication is required for either endpoint (Req 1.4).

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

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from admin_portal.db.session import get_db
from admin_portal.schemas.health import HealthResponse, ReadinessResponse

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


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Portal readiness check (Postgres connectivity)",
    description=(
        "Returns HTTP 200 with status=ready when Postgres is reachable. "
        "Returns HTTP 503 with status=not_ready otherwise, or if a startup "
        "failure was recorded."
    ),
)
async def readiness_check(db: Session = Depends(get_db)) -> JSONResponse:
    """Return whether the Portal can actually serve DB-backed requests.

    - HTTP 200: Postgres is reachable.
    - HTTP 503: startup failure was detected, or Postgres is unreachable.
    """
    if _startup_failure_reason is not None:
        body = ReadinessResponse(status="degraded", reason=_startup_failure_reason)
        return JSONResponse(status_code=503, content=body.model_dump())

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any DB failure means "not ready"
        body = ReadinessResponse(status="not_ready", reason=f"database unreachable: {exc}")
        return JSONResponse(status_code=503, content=body.model_dump())

    body = ReadinessResponse(status="ready")
    return JSONResponse(status_code=200, content=body.model_dump())
