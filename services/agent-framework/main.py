"""
services/agent-framework/main.py

FastAPI application factory and lifespan handler for the Agent Framework (Layer 6).

This is a stub for Task 1 — the full lifespan (tool registry loading, env var
validation) is wired up in Task 9. The app is importable and startable now.

Ports:
  - Main API:      8083  (app)
  - Prometheus:    9090  (metrics_app)
"""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_framework.logging_config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle handler.

    Full startup validation (env vars, tool catalog) added in Task 9.
    """
    from agent_framework.config import settings

    if settings is None:
        logger.error("Failed to load settings; refusing to start")
        sys.exit(1)

    logger.info(
        "Agent Framework starting",
        extra={
            "extra_fields": {
                "port": settings.port,
                "metrics_port": settings.metrics_port,
            }
        },
    )
    yield
    logger.info("Agent Framework stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Agent Framework", version="0.1.0", lifespan=lifespan)

    from agent_framework.routers import agent, health

    app.include_router(health.router)
    app.include_router(agent.router)

    return app


# ASGI application instances
app = create_app()

# Separate Prometheus metrics ASGI app on port 9090.
# Importing metrics registers all counters/histograms in the default registry.
from prometheus_client import make_asgi_app as _make_metrics_app  # noqa: E402
import agent_framework.metrics  # noqa: F401, E402 — registers metrics in default registry

metrics_app = _make_metrics_app()
