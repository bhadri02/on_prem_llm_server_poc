"""
intelligent_router/main.py

FastAPI app factory and lifespan handler for the Intelligent Router (Layer 3).

Performs ordered startup validation before accepting requests:
  1. Validates required env vars (MODEL_MATRIX_PATH, TASK_RULES_PATH, AUDIT_STORE_URL)
  2. Validates numeric ranges (INFERENCE_TIMEOUT_SECONDS, HEALTH_CHECK_TIMEOUT_SECONDS)
  3. Loads task classifier rules from YAML
  4. Loads model matrix from YAML
  5. Creates a shared httpx.AsyncClient
  6. Stores all state on app.state and emits a startup INFO log

Custom exception handler maps RequestValidationError to HTTP 400 (JSON parse errors)
or HTTP 422 (other validation errors).

Requirements: 1.7, 1.8, 2.5, 2.6, 3.4, 3.5, 14.2, 14.3, 14.4, 14.7, 14.8, 15.1, 15.2
"""

import asyncio
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from intelligent_router.audit_client import flush_pending_audit_events
from intelligent_router.config import settings
from intelligent_router.logging_config import get_logger
from intelligent_router.model_selector import load_model_matrix
from intelligent_router.policy import load_policy_matrix
from intelligent_router.routers.health import health_router
from intelligent_router.routers.openai_compat import openai_router
from intelligent_router.routers.route import route_router
from intelligent_router.task_classifier import load_classifier_rules

# ---------------------------------------------------------------------------
# Configure shared observability logging at module level (Requirements 6.1–6.6)
# ---------------------------------------------------------------------------
from shared.observability.logging import configure_structlog

if settings is not None:
    configure_structlog("router", settings.log_level)
else:
    configure_structlog("router", "INFO")

# ---------------------------------------------------------------------------
# Configure distributed tracing (opt-in, disabled by default for POC).
# ---------------------------------------------------------------------------
from shared.observability.middleware import configure_tracing

if settings is not None and settings.tracing_enabled:
    configure_tracing("router", settings.otel_endpoint)

logger = get_logger(__name__)


async def _audit_flush_loop(app: FastAPI) -> None:
    """Background loop: periodically retries audit events that exhausted
    post_audit_event's own retries (see audit_client.py's _pending queue).
    Never crashes the service on failure."""
    while True:
        await asyncio.sleep(settings.audit_flush_interval_seconds)
        try:
            await flush_pending_audit_events(
                settings.audit_store_url, app.state.http_client, settings.audit_api_key
            )
        except Exception as exc:  # noqa: BLE001 — never let this loop die
            logger.error("audit_flush_loop_failed", extra={"extra_fields": {"error": str(exc)}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async lifespan context manager for startup validation and shutdown cleanup.

    Performs all startup checks and resource initialisation before yielding.
    Cleans up the shared HTTP client on shutdown.

    Args:
        app: The FastAPI application instance whose state will be populated.
    """
    # ------------------------------------------------------------------
    # Guard: settings may be None when required env vars are absent
    # (config.py wraps Settings() in try/except for test-safe imports)
    # ------------------------------------------------------------------
    if settings is None:
        logger.error(
            "Required env vars missing — MODEL_MATRIX_PATH, TASK_RULES_PATH, "
            "or AUDIT_STORE_URL is not set; refusing to start"
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 1: Validate required env vars are non-empty strings
    # ------------------------------------------------------------------
    for field in ("model_matrix_path", "task_rules_path", "audit_store_url"):
        if not getattr(settings, field):
            logger.error(
                f"{field.upper()} is not set or empty; refusing to start"
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Validate numeric timeout ranges
    # ------------------------------------------------------------------
    if not (1 <= settings.inference_timeout_seconds <= 600):
        logger.error(
            "INFERENCE_TIMEOUT_SECONDS out of range [1,600]; refusing to start"
        )
        sys.exit(1)

    if not (1 <= settings.health_check_timeout_seconds <= 30):
        logger.error(
            "HEALTH_CHECK_TIMEOUT_SECONDS out of range [1,30]; refusing to start"
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Load task classifier rules
    # ------------------------------------------------------------------
    classifier_rules = load_classifier_rules(settings.task_rules_path)
    if classifier_rules is None:
        # load_classifier_rules already logged the specific failure
        sys.exit(1)

    if not classifier_rules.rules:
        logger.warning(
            "Task classifier rules map is empty; all requests classified as 'chat'"
        )

    # ------------------------------------------------------------------
    # Step 4: Load model matrix
    # ------------------------------------------------------------------
    model_matrix = load_model_matrix(settings.model_matrix_path)
    if model_matrix is None:
        # load_model_matrix already logged the specific failure
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4b: Load policy matrix (Phase 2 — RBAC role/task_type enforcement)
    # ------------------------------------------------------------------
    policy_matrix = load_policy_matrix(settings.policy_matrix_path)
    if policy_matrix is None:
        # load_policy_matrix already logged the specific failure
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 5: Create shared httpx client
    # ------------------------------------------------------------------
    http_client = httpx.AsyncClient()

    # ------------------------------------------------------------------
    # Step 6: Store everything on app.state and emit startup log
    # ------------------------------------------------------------------
    app.state.settings = settings
    app.state.classifier_rules = classifier_rules
    app.state.model_matrix = model_matrix
    app.state.policy_matrix = policy_matrix
    app.state.http_client = http_client

    flush_task = asyncio.create_task(_audit_flush_loop(app))

    logger.info(
        "Intelligent Router started",
        extra={
            "extra_fields": {
                "rules_loaded": classifier_rules.total_keyword_count,
                "models_loaded": len(model_matrix.models),
                "policy_roles_loaded": len(policy_matrix.roles),
            }
        },
    )

    yield

    # ------------------------------------------------------------------
    # Shutdown: cancel the audit flush loop, close the shared HTTP client
    # ------------------------------------------------------------------
    flush_task.cancel()
    try:
        await flush_task
    except asyncio.CancelledError:
        pass
    await http_client.aclose()
    logger.info("Intelligent Router stopped")


def create_app() -> FastAPI:
    """Factory function that constructs and returns a fully configured FastAPI app.

    Used both as the module-level entrypoint (uvicorn) and by tests so that
    each test can get a fresh app instance with its own lifespan.

    Returns:
        A FastAPI application with lifespan, custom exception handler, and all
        routers registered.
    """
    application = FastAPI(
        lifespan=lifespan,
        title="Intelligent Router",
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Custom exception handler for RequestValidationError
    #
    # Returns HTTP 400 for JSON parse errors (type == "json_invalid"), so
    # callers get a clear signal that their request body was not valid JSON.
    # All other Pydantic validation failures return HTTP 422 as usual.
    # ------------------------------------------------------------------
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()

        # Detect JSON parse errors by checking for the "json_invalid" error type
        if any(e.get("type") == "json_invalid" for e in errors):
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_json", "request_id": None},
            )

        # Other validation errors — attempt to extract request_id from the body
        request_id = None
        try:
            body = await request.json()
            request_id = body.get("request_id")
        except Exception:
            pass

        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "request_id": request_id},
        )

    # ------------------------------------------------------------------
    # Router wiring
    # ------------------------------------------------------------------
    application.include_router(route_router)
    application.include_router(openai_router)
    application.include_router(health_router)
    application.mount("/metrics", make_asgi_app())

    return application


# Module-level app instance used by uvicorn:
#   uvicorn intelligent_router.main:app --host 0.0.0.0 --port 8082
app = create_app()
