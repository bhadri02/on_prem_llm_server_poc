"""
Health router for the Cache Service (Layer 4).

Exposes ``GET /health`` with no authentication required. The endpoint
implements a state machine driven by two module-level flags that are
mutated by the application lifespan in ``cache_service/main.py``:

  _ready                   — set to True only after all startup steps succeed
  _startup_failure_reason  — set to a reason string if a startup step fails

State machine (evaluated in priority order):
  1. _ready is False          → 503 {"status": "starting"}
  2. _startup_failure_reason == "embedding_model_load_failed"
                              → 503 {"status": "unavailable",
                                     "reason": "embedding_model_load_failed"}
  3. Redis ping succeeds      → 200 {"status": "ok"}
  4. Redis ping fails / redis is None
                              → 503 {"status": "unavailable",
                                     "reason": "redis_unreachable"}

Validates: Requirements 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Module-level state flags
# Mutated by cache_service/main.py during the async lifespan startup sequence.
# ---------------------------------------------------------------------------

_ready: bool = False
_startup_failure_reason: str | None = None

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Return the current health status of the Cache Service.

    Priority order:
      1. Service not yet ready                   → 503 starting
      2. Embedding model failed to load          → 503 unavailable (embedding)
      3. Redis reachable                         → 200 ok
      4. Redis unreachable or not initialised    → 503 unavailable (redis)

    No authentication is required on this endpoint.
    """
    # --- (1) Service still initialising ---
    if not _ready:
        return JSONResponse(
            status_code=503,
            content={"status": "starting"},
        )

    # --- (2) Embedding model load failure ---
    if _startup_failure_reason == "embedding_model_load_failed":
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "reason": "embedding_model_load_failed",
            },
        )

    # --- (3 & 4) Ping Redis ---
    try:
        redis = request.app.state.redis
        if redis is None:
            raise RuntimeError("Redis client is None")
        await redis.ping()
        return JSONResponse(
            status_code=200,
            content={"status": "ok"},
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "reason": "redis_unreachable",
            },
        )
