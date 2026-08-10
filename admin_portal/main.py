"""
admin_portal/main.py

FastAPI application factory for the Admin/Developer Portal (Layer 10).

This module:
  - Creates the FastAPI app with a lifespan handler for startup validation.
  - Registers ``LoggingMiddleware`` for structured JSON request logs.
  - Mounts the Prometheus metrics ASGI app at ``/metrics`` (port 8084 path;
    also serve separately on port 9090 via a dedicated ASGI server if needed).
  - Registers all routers under the ``/portal`` prefix.

Startup validation (lifespan)
------------------------------
``config.py`` already calls ``sys.exit(1)`` at module-import time if
``GATEWAY_API_KEY`` is absent, so by the time the lifespan hook runs the
key is guaranteed to be present.  The lifespan is retained as the canonical
place to add further startup checks (e.g. soft-dependency probes) and to
set the startup-failure flag in ``health.py`` for degraded-but-alive states.

Port layout
-----------
  8084  — Portal_API (all /portal/* routes + /metrics path)
  9090  — Prometheus scrape target (served by a separate ``uvicorn`` process
           or by mounting ``metrics_app`` on a second ASGI app; for POC the
           /metrics path on 8084 satisfies Req 1.5 and 10.2)

Validates: Requirements 1.5, 2.9, 10.2
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from starlette.routing import Mount

from admin_portal.metrics import metrics_app
from admin_portal.middleware.logging import LoggingMiddleware
from admin_portal.routers import auth as auth_router_module
from admin_portal.routers import health as health_router_module
from admin_portal.routers import config as config_router_module
from admin_portal.routers import playground as playground_router_module
from admin_portal.routers import audit as audit_router_module
from admin_portal.routers import models as models_router_module
from admin_portal.routers import ollama_admin as ollama_admin_router_module
from admin_portal.routers import policy as policy_router_module
from admin_portal.routers import metrics_summary as metrics_summary_router_module
from admin_portal.routers import keys as keys_router_module
from admin_portal.routers import users as users_router_module
from admin_portal.routers import roles as roles_router_module
from admin_portal.routers import chat as chat_router_module

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.

    Startup
    -------
    config.py validates GATEWAY_API_KEY at import time and calls sys.exit(1)
    if it is absent, so we only reach this point when the key is present.
    Additional soft-dependency checks can be added here; on failure call
    ``health_router_module.set_startup_failure(reason)`` and continue (the
    health endpoint will return 503 but the pod stays alive for debugging).

    Shutdown
    --------
    Add cleanup logic (e.g. close shared httpx clients) below the ``yield``.
    """
    # --- Startup ---
    _logger.info("Portal_API starting up.")

    # Import settings here (already validated at module import; if we reach
    # this point GATEWAY_API_KEY is confirmed present).
    try:
        from admin_portal.config import settings  # noqa: F401 — import validates
        _logger.info(
            "Config validated. API_GATEWAY_URL=%s LOG_LEVEL=%s",
            settings.API_GATEWAY_URL,
            settings.LOG_LEVEL,
        )

        # --- Users/roles/API-keys DB (Phase 1) --------------------------
        # Idempotent: safe to run on every boot. Failure here is a hard
        # startup failure — the resolve endpoint (and therefore all
        # authenticated traffic through the Gateway) depends on this DB.
        from admin_portal.db.migrations import run_additive_migrations
        from admin_portal.db.models import Base
        from admin_portal.db.seed import run_startup_seed
        from admin_portal.db.session import SessionLocal, engine

        Base.metadata.create_all(engine)
        run_additive_migrations(engine)
        db = SessionLocal()
        try:
            run_startup_seed(db, settings.GATEWAY_API_KEY, settings.SEED_ADMIN_PASSWORD)
        finally:
            db.close()
        _logger.info("Users/roles/API-keys DB ready and seeded.")
    except SystemExit:
        # config.py already called sys.exit(1) — propagate.
        raise
    except Exception as exc:  # pragma: no cover
        reason = f"Config validation error: {exc}"
        _logger.error(reason)
        health_router_module.set_startup_failure(reason)

    yield

    # --- Shutdown ---
    _logger.info("Portal_API shutting down.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the Portal_API FastAPI application."""
    app = FastAPI(
        title="Portal API",
        description="Admin/Developer Portal reverse-proxy API (Layer 10)",
        version="0.1.0",
        lifespan=lifespan,
        # Disable the default /docs and /redoc paths exposing internal details
        # in production; keep them for POC convenience.
        docs_url="/portal/docs",
        redoc_url="/portal/redoc",
        openapi_url="/portal/openapi.json",
    )

    # ------------------------------------------------------------------
    # Middleware (registered in reverse application order — last registered
    # is outermost, i.e. first to intercept requests)
    # ------------------------------------------------------------------
    app.add_middleware(LoggingMiddleware)

    # ------------------------------------------------------------------
    # Prometheus metrics — mount at /metrics on the main app (port 8084).
    # Req 1.5 / 10.2: also exposed on port 9090 via a separate uvicorn
    # process (see Dockerfile / Helm chart CMD).  For POC a single mount
    # here satisfies the acceptance criteria.
    # ------------------------------------------------------------------
    app.mount("/metrics", metrics_app)

    # ------------------------------------------------------------------
    # Routers — all registered under the /portal prefix
    # ------------------------------------------------------------------
    _portal_prefix = "/portal"

    app.include_router(auth_router_module.router, prefix=_portal_prefix)
    app.include_router(health_router_module.router, prefix=_portal_prefix)
    app.include_router(config_router_module.router, prefix=_portal_prefix)
    app.include_router(playground_router_module.router, prefix=_portal_prefix)
    app.include_router(audit_router_module.router, prefix=_portal_prefix)
    app.include_router(models_router_module.router, prefix=_portal_prefix)
    app.include_router(ollama_admin_router_module.router, prefix=_portal_prefix)
    app.include_router(policy_router_module.router, prefix=_portal_prefix)
    app.include_router(metrics_summary_router_module.router, prefix=_portal_prefix)
    app.include_router(keys_router_module.router, prefix=_portal_prefix)
    app.include_router(users_router_module.router, prefix=_portal_prefix)
    app.include_router(roles_router_module.router, prefix=_portal_prefix)
    app.include_router(chat_router_module.router, prefix=_portal_prefix)

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by uvicorn: ``admin_portal.main:app``)
# ---------------------------------------------------------------------------
app = create_app()
