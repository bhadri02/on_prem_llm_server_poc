"""
main.py — FastAPI app factory and lifespan handler for the Security & Governance Layer.

Responsible for:
- Startup validation of required environment variables
- Loading and compiling injection patterns
- Initialising Presidio PII engines (if PII_ENABLED)
- Storing all startup state on ``app.state``
- Wiring the three routers (pre-check, post-check, health)
- Custom exception handler for ``RequestValidationError``
- ``create_app()`` factory used by tests and the uvicorn entrypoint
"""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

from security_layer.config import settings
from security_layer.content_safety import BLOCKLIST
from security_layer.injection import load_injection_patterns
from security_layer.logging_config import get_logger
from security_layer.routers.health import router as health_router
from security_layer.routers.post_check import router as post_check_router
from security_layer.routers.pre_check import router as pre_check_router

# ---------------------------------------------------------------------------
# Configure shared observability logging at module level (Requirements 6.1–6.6)
# ---------------------------------------------------------------------------
from shared.observability.logging import configure_structlog

configure_structlog("security", settings.log_level)

# ---------------------------------------------------------------------------
# Configure distributed tracing (opt-in, disabled by default for POC).
# ---------------------------------------------------------------------------
from shared.observability.middleware import configure_tracing

if settings.tracing_enabled:
    configure_tracing("security", settings.otel_endpoint)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 18.1  Lifespan async context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Perform all startup validation and resource initialisation before serving.

    Steps (in order):
        1. Validate that every required env var is non-empty.
        2. Load and compile injection patterns from the configured YAML file.
        3. Initialise Presidio ``AnalyzerEngine`` / ``AnonymizerEngine`` when
           ``PII_ENABLED=true``; set both to ``None`` when disabled.
        4. Store all state on ``app.state`` and log the startup confirmation.

    Yields control to the ASGI framework once startup is complete.  On
    shutdown, logs "Security Layer stopped".

    Exits the process (``sys.exit(1)``) on any unrecoverable startup failure
    so Kubernetes restarts the pod rather than serving requests in a broken
    state.
    """
    # ------------------------------------------------------------------
    # Step 1 — Validate required env vars are non-empty
    # ------------------------------------------------------------------
    required_fields = (
        "downstream_router_url",
        "audit_store_url",
        "audit_api_key",
        "injection_patterns_path",
    )
    for field in required_fields:
        if not getattr(settings, field):
            logger.error(
                f"{field.upper()} is not set or empty; refusing to start",
                extra={"extra_fields": {"field": field}},
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2 — Load and compile injection patterns
    # ------------------------------------------------------------------
    patterns = load_injection_patterns(settings.injection_patterns_path)
    if patterns is None:
        # load_injection_patterns already logged the specific failure reason.
        sys.exit(1)
    if len(patterns) == 0:
        logger.warning(
            "Injection patterns list is empty; all requests pass injection check"
        )

    # ------------------------------------------------------------------
    # Step 3 — Initialise Presidio engines (only when PII_ENABLED)
    # ------------------------------------------------------------------
    analyzer = None
    anonymizer = None
    if settings.pii_enabled:
        try:
            analyzer = AnalyzerEngine()
            anonymizer = AnonymizerEngine()
        except Exception as exc:
            logger.error(
                f"Failed to initialise Presidio: {exc}; refusing to start",
                extra={"extra_fields": {"error": str(exc)}},
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4 — Store all state on app.state and log startup confirmation
    # ------------------------------------------------------------------
    app.state.settings = settings
    app.state.patterns = patterns
    app.state.analyzer = analyzer
    app.state.anonymizer = anonymizer
    app.state.blocklist = BLOCKLIST

    logger.info(
        "Security Layer started",
        extra={
            "extra_fields": {
                "pii_enabled": settings.pii_enabled,
                "patterns_loaded": len(patterns),
            }
        },
    )

    yield

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    logger.info("Security Layer stopped")


# ---------------------------------------------------------------------------
# 18.2  FastAPI application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    lifespan=lifespan,
    title="Security & Governance Layer",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# 18.3  Custom exception handler for RequestValidationError
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return HTTP 400 for unparseable JSON bodies, HTTP 422 for all other
    Pydantic validation failures.

    FastAPI raises ``RequestValidationError`` both when the request body
    cannot be decoded as JSON (type ``"json_invalid"``) and when the decoded
    JSON does not conform to the expected schema.  Callers need to be able to
    distinguish between a bad payload format (400) and a schema violation
    (422), so this handler inspects the error list and picks the appropriate
    status code.

    Args:
        request: The incoming :class:`fastapi.Request`.
        exc:     The :class:`RequestValidationError` raised by FastAPI.

    Returns:
        A :class:`JSONResponse` with ``{"detail": exc.errors()}`` and the
        appropriate HTTP status code.
    """
    errors = exc.errors()
    # Check for a JSON decode error among the reported validation failures.
    is_json_error = any(err.get("type") == "json_invalid" for err in errors)
    status_code = 400 if is_json_error else 422
    return JSONResponse(status_code=status_code, content={"detail": errors})


# ---------------------------------------------------------------------------
# 18.4  Include routers
# ---------------------------------------------------------------------------

app.include_router(pre_check_router)
app.include_router(post_check_router)
app.include_router(health_router)


# ---------------------------------------------------------------------------
# 18.5  Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Return the configured FastAPI application instance.

    Used by tests and the uvicorn entrypoint.  Does NOT create a new
    application — it returns the module-level ``app`` that has already been
    fully configured with its lifespan handler, exception handler, and routers.

    Returns:
        The module-level :class:`FastAPI` instance.
    """
    return app
