"""
agent_framework/main.py

FastAPI application factory and lifespan handler for the Agent Framework (Layer 6).

Startup validation (lifespan):
  1. Validates router_url, gateway_api_key, tool_catalog_path are non-empty.
  2. Validates max_agent_steps is in range [1, 50].
  3. Loads tool registry via load_tool_registry(); sys.exit(1) if None.
  4. Stores settings and tool_registry on app.state.

Ports:
  - Main API:      8083  (app)
  - Prometheus:    9090  (metrics_app)

Requirements: 1.1, 1.5, 1.6, 5.3, 13.1
"""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

import agent_framework.metrics  # noqa: F401 — registers Prometheus counters
from agent_framework.config import settings
from agent_framework.logging_config import get_logger
from agent_framework.tools.registry import load_tool_registry

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle handler.

    Validates all required settings, loads the tool registry, and stores
    both on app.state so routers can access them via request.app.state.
    Calls sys.exit(1) on any failure before the HTTP listener starts.
    """
    # 1. Validate required env vars are non-empty (Req 5.3)
    for field in ("router_url", "gateway_api_key", "tool_catalog_path"):
        if not getattr(settings, field, None):
            logger.error(
                "%s is not set or empty; refusing to start",
                field.upper(),
            )
            sys.exit(1)

    # 2. Validate max_agent_steps is within [1, 50]
    if not (1 <= settings.max_agent_steps <= 50):
        logger.error(
            "MAX_AGENT_STEPS=%d is out of range [1, 50]; refusing to start",
            settings.max_agent_steps,
        )
        sys.exit(1)

    # 3. Load tool catalog — fail fast if missing or invalid (Req 5.3)
    tool_registry = load_tool_registry(settings.tool_catalog_path)
    if tool_registry is None:
        # load_tool_registry already logged the specific failure
        sys.exit(1)

    # 4. Store on app.state for use by request handlers
    app.state.settings = settings
    app.state.tool_registry = tool_registry

    logger.info(
        "Agent Framework started",
        extra={
            "extra_fields": {
                "router_url": settings.router_url,
                "max_agent_steps": settings.max_agent_steps,
                "tools_loaded": list(tool_registry.keys()),
                "port": settings.port,
                "metrics_port": settings.metrics_port,
            }
        },
    )

    yield

    logger.info("Agent Framework stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="Agent Framework",
        version="0.1.0",
        lifespan=lifespan,
    )

    from agent_framework.routers import agent, health

    application.include_router(health.router)
    application.include_router(agent.router)

    return application


# Module-level ASGI application instances consumed by uvicorn.
app = create_app()

# Separate Prometheus metrics ASGI app (port 9090).
# The import of agent_framework.metrics above already registered all counters
# in the default prometheus_client registry; make_asgi_app() exposes them.
from prometheus_client import make_asgi_app as _make_metrics_app  # noqa: E402

metrics_app = _make_metrics_app()
