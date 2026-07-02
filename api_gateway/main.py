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

import json
import sys
import traceback as tb_module
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
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
# Lifespan — manage shared httpx.AsyncClient across requests
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create and tear down the shared HTTP client.

    On startup: instantiates ``httpx.AsyncClient`` and stores it on
    ``app.state.http_client`` so route handlers can retrieve it via
    ``request.app.state.http_client``.

    On shutdown: cleanly closes the client and its connection pool.
    """
    app.state.http_client = httpx.AsyncClient()
    try:
        yield
    finally:
        await app.state.http_client.aclose()


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

        Emits a structured JSON ERROR log record to stdout containing:
            level, request_id, exception_type, exception_message, traceback

        Returns HTTP 500 with the canonical error body.
        """
        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())

        error_record = {
            "level": "ERROR",
            "request_id": request_id,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": tb_module.format_exc(),
        }
        try:
            print(json.dumps(error_record), flush=True)
        except Exception:
            pass  # silently discard on stdout failure

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
