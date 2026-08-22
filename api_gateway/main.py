"""
FastAPI application factory and wiring for the API Gateway (Layer 1).

Creates the FastAPI app, registers middleware (in reverse order to achieve
the correct execution order), includes routers, mounts the Prometheus ASGI
app, and registers exception handlers.

Middleware execution order (outermost → innermost on inbound requests):
    PrometheusMiddleware → LoggingMiddleware → AuthMiddleware → RateLimitMiddleware → Router

Startup validation: get_settings() is called at module level; if
GATEWAY_API_KEY is missing or empty, pydantic raises ValidationError, a
structured JSON error is emitted, and the process exits with code 1.

Validates: Requirements 1.4, 1.7, 2.1, 8.5, 10.1
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback as tb_module
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from api_gateway.middleware.auth import AuthMiddleware
from api_gateway.middleware.logging import LoggingMiddleware
from api_gateway.middleware.prometheus import PrometheusMiddleware
from api_gateway.middleware.rate_limit import RateLimitMiddleware
from api_gateway.routers.chat import router as chat_router
from api_gateway.routers.chat import validation_exception_handler
from api_gateway.routers.health import router as health_router
from api_gateway.routers.models import router as models_router
from api_gateway.services.audit_client import flush_pending_audit_events

# ---------------------------------------------------------------------------
# Startup validation — fail fast if required configuration is missing.
# Wrap in try/except so we can emit a structured error before exiting.
# ---------------------------------------------------------------------------
try:
    from api_gateway.config import get_settings

    settings = get_settings()
except Exception as exc:
    _error_record = {
        "level": "ERROR",
        "event": "startup_validation_failed",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "traceback": tb_module.format_exc(),
    }
    try:
        print(json.dumps(_error_record), flush=True)
    except Exception:
        pass
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configure shared observability logging on startup.
# This configures structlog globally for the api_gateway service, reading
# the LOG_LEVEL from settings (which in turn reads the LOG_LEVEL env var).
# ---------------------------------------------------------------------------
from shared.observability.logging import configure_structlog, emit, get_logger

configure_structlog("api_gateway", settings.log_level)

# ---------------------------------------------------------------------------
# Configure distributed tracing (opt-in, disabled by default for POC).
# Guards the configure_tracing() call behind the TRACING_ENABLED env var
# (default False).  When enabled, wires OTel instrumentation into FastAPI
# and httpx so that traces span all layers.  Gracefully no-ops when
# opentelemetry-* packages are not installed (Requirement 9.1).
# ---------------------------------------------------------------------------
from shared.observability.middleware import configure_tracing

if settings.tracing_enabled:
    configure_tracing("api_gateway", settings.otel_endpoint)


# ---------------------------------------------------------------------------
# Lifespan — manage shared httpx.AsyncClient and Redis client across requests
# ---------------------------------------------------------------------------


async def _audit_flush_loop(app: FastAPI) -> None:
    """Background loop: periodically retries audit events that exhausted
    post_audit_event's own retries (see services/audit_client.py's
    _pending queue). Never crashes the service on failure."""
    logger = get_logger("audit-flush")
    while True:
        await asyncio.sleep(settings.audit_flush_interval_seconds)
        try:
            await flush_pending_audit_events(app.state.http_client)
        except Exception as exc:  # noqa: BLE001 — never let this loop die
            emit(logger, level="ERROR", event="audit_flush_loop_failed", message=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create and tear down the shared HTTP and Redis clients.

    On startup: instantiates ``httpx.AsyncClient`` (app.state.http_client)
    and a ``redis.asyncio.Redis`` client (app.state.redis, backing
    RateLimitMiddleware's per-key counters) so route handlers/middleware
    can retrieve them via ``request.app.state.*``.

    On shutdown: cleanly closes both clients and their connection pools.
    """
    app.state.http_client = httpx.AsyncClient()
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    flush_task = asyncio.create_task(_audit_flush_loop(app))
    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        await app.state.http_client.aclose()
        await app.state.redis.aclose()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Registers middleware in reverse registration order so that the execution
    order on inbound requests is:
        PrometheusMiddleware → LoggingMiddleware → AuthMiddleware → RateLimitMiddleware → Router

    Returns:
        A fully configured :class:`FastAPI` application instance.
    """
    app = FastAPI(lifespan=lifespan)

    # ------------------------------------------------------------------
    # Middleware — registered in reverse execution order.
    # Starlette applies middleware in reverse registration order (last-added
    # becomes outermost wrapper and executes first on inbound requests).
    # ------------------------------------------------------------------
    app.add_middleware(RateLimitMiddleware)   # registered first → innermost → runs last
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(PrometheusMiddleware)  # registered last → outermost → runs first

    # ------------------------------------------------------------------
    # Routers — paths already include /v1/... and /health prefixes
    # ------------------------------------------------------------------
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)

    # ------------------------------------------------------------------
    # Prometheus ASGI sub-application mounted at /metrics
    # ------------------------------------------------------------------
    app.mount("/metrics", make_asgi_app())

    # ------------------------------------------------------------------
    # Exception handlers
    # ------------------------------------------------------------------

    # RequestValidationError → HTTP 400 (overrides FastAPI's default 422)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Global catch-all for any unhandled exception that escapes the middleware
    # stack entirely.  Emits a structured ERROR log and returns JSON 500.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Handle any exception that escapes the middleware stack.

        Emits a structured JSON ERROR log record to stdout using shared
        observability logging (emit), containing: request_id, exception_type.

        Returns HTTP 500 with the canonical error body.
        """
        from shared.observability.logging import emit, get_logger

        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        logger = get_logger(request_id)

        emit(
            logger,
            level="ERROR",
            event="unhandled_exception",
            message=f"Unhandled exception: {type(exc).__name__}",
            exception_type=type(exc).__name__,
        )

        return JSONResponse(
            status_code=500,
            content={"error": {"code": "500", "message": "Internal server error"}},
        )

    return app


# ---------------------------------------------------------------------------
# Module-level app instance — enables: uvicorn api_gateway.main:app
# ---------------------------------------------------------------------------
app = create_app()


# ---------------------------------------------------------------------------
# Direct execution entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_gateway.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
