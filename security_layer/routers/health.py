"""
routers/health.py — GET /health handler.

Implements the Kubernetes liveness probe health endpoint.  No authentication
is required on this endpoint.

The endpoint inspects three pieces of app state set during the lifespan
startup handler:

- ``state.settings.pii_enabled`` — whether Presidio PII detection is active.
- ``state.patterns`` — the compiled injection pattern list (count must be > 0).
- ``state.analyzer`` — the Presidio ``AnalyzerEngine`` instance (``None`` if
  not yet initialised or if initialisation failed).

Response codes:

- **HTTP 200** ``{"status": "ok", "pii_enabled": <bool>, "patterns_loaded": <int>}``
  when Presidio is available (or PII is disabled) AND at least one injection
  pattern is loaded.
- **HTTP 503** ``{"status": "degraded", "reason": "presidio_unavailable"}``
  when ``pii_enabled=True`` and ``state.analyzer is None``.
- **HTTP 503** ``{"status": "degraded", "reason": "no_patterns_loaded"}``
  when ``len(state.patterns) == 0`` (checked only after confirming Presidio
  is OK — ``presidio_unavailable`` takes priority if both fail).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Return the operational health of the Security & Governance Layer.

    Checks whether the Presidio ``AnalyzerEngine`` is initialised (or PII is
    disabled) and whether at least one injection pattern is loaded.  Returns
    HTTP 200 when both conditions are satisfied, HTTP 503 otherwise.

    No API key or other authentication credential is required.

    Args:
        request: FastAPI :class:`Request` giving access to ``app.state``.

    Returns:
        A :class:`JSONResponse` with HTTP 200 when healthy, or HTTP 503 with
        a ``reason`` field identifying the first failing condition.
    """
    state = request.app.state
    pii_enabled: bool = state.settings.pii_enabled
    patterns_loaded: int = len(state.patterns)

    # presidio_ok is True when PII detection is disabled (no engine needed)
    # or when the AnalyzerEngine was successfully initialised at startup.
    presidio_ok: bool = (not pii_enabled) or (state.analyzer is not None)

    if presidio_ok and patterns_loaded > 0:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "pii_enabled": pii_enabled,
                "patterns_loaded": patterns_loaded,
            },
        )

    # presidio_unavailable takes priority when both conditions fail.
    reason = "presidio_unavailable" if not presidio_ok else "no_patterns_loaded"
    return JSONResponse(
        status_code=503,
        content={
            "status": "degraded",
            "reason": reason,
        },
    )
