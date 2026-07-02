"""
intelligent_router/routers/health.py

GET /health — lightweight liveness/readiness probe for the Intelligent Router.

Returns:
  - HTTP 200 {"status": "ok", "rules_loaded": <int>, "models_loaded": <int>}
    when both task_classifier_rules and model_matrix have been loaded
    successfully into app.state.
  - HTTP 503 {"status": "degraded", "reason": "rules_load_failed"}
    when classifier_rules is None (failed to load at startup).
  - HTTP 503 {"status": "degraded", "reason": "matrix_load_failed"}
    when model_matrix is None (failed to load at startup).

No authentication is required and no downstream calls (Cache, Inference,
Audit) are made — this endpoint reads only from in-process app.state.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

health_router = APIRouter()


@health_router.get("/health")
async def get_health(request: Request) -> JSONResponse:
    """Return the operational status of the Intelligent Router.

    Reads ``classifier_rules`` and ``model_matrix`` from ``request.app.state``
    — both are set by the lifespan handler before the HTTP listener accepts
    connections.

    Args:
        request: FastAPI Request used to access ``app.state``.

    Returns:
        JSONResponse with HTTP 200 when both config objects are loaded, or
        HTTP 503 with a ``reason`` field identifying which config failed.
    """
    classifier_rules = getattr(request.app.state, "classifier_rules", None)
    model_matrix = getattr(request.app.state, "model_matrix", None)

    if classifier_rules is None:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "reason": "rules_load_failed"},
        )

    if model_matrix is None:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "reason": "matrix_load_failed"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "rules_loaded": classifier_rules.total_keyword_count,
            "models_loaded": len(model_matrix.models),
        },
    )
