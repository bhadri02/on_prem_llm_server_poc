"""
Application factory for the Cache Service (Layer 4).

Wires together the FastAPI app, async lifespan context manager, middleware
(LoggingMiddleware), and routers (health, cache).  Also starts a secondary
uvicorn server on port ``settings.port + 1`` (default 9090) to serve
Prometheus metrics via ``prometheus_client.make_asgi_app()``.

Startup sequence (inside lifespan):
  1. Load Settings.
  2. Connect to Redis — store on app.state.redis; on failure, set startup
     failure reason and continue (do NOT exit).
  3. Load EmbeddingGenerator — store on app.state.embedding_generator; on
     EmbeddingLoadError, set startup failure reason and continue.
  4. Instantiate ExactCacheService  → app.state.exact_cache.
  5. Instantiate SemanticCacheService → app.state.semantic_cache.
  6. Set health._ready = True so /health returns 200.
  7. yield (service is running).
  8. Shutdown: close Redis connection if open.

Validates: Requirements 6.1, 6.2–6.5, 6.8, 9.1
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

import cache_service.routers.health as health
from cache_service.config import get_settings
from cache_service.exceptions import EmbeddingLoadError
from cache_service.middleware.logging import LoggingMiddleware
from cache_service.routers.cache import router as cache_router
from cache_service.routers.health import router as health_router
from cache_service.services.embedding import EmbeddingGenerator
from cache_service.services.exact_cache import ExactCacheService
from cache_service.services.semantic_cache import SemanticCacheService

# ---------------------------------------------------------------------------
# Configure shared observability logging at module level (Requirements 6.1–6.6)
# ---------------------------------------------------------------------------
from shared.observability.logging import configure_structlog

_settings_for_log = get_settings()
configure_structlog("cache", _settings_for_log.log_level)

# ---------------------------------------------------------------------------
# Configure distributed tracing (opt-in, disabled by default for POC).
# ---------------------------------------------------------------------------
from shared.observability.middleware import configure_tracing

if _settings_for_log.tracing_enabled:
    configure_tracing("cache", _settings_for_log.otel_endpoint)


# ---------------------------------------------------------------------------
# Structured log helper
# ---------------------------------------------------------------------------

def _log(entry: dict) -> None:
    """Emit a structured JSON log entry to stdout. Silently discards on error."""
    try:
        print(json.dumps(entry), flush=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async context manager managing application startup and shutdown.

    Startup:
      - Connects to Redis; stores client on app.state.redis.
      - Loads the sentence-transformer embedding model.
      - Instantiates ExactCacheService and SemanticCacheService.
      - Sets health._ready = True once all non-critical steps complete.
      - Starts a background Prometheus metrics server on port+1.

    Shutdown:
      - Closes the Redis connection if it was opened.
      - Cancels the metrics server task.
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # 1. Reset health flags so a restart cycle starts cleanly.
    # ------------------------------------------------------------------
    health._ready = False
    health._startup_failure_reason = None

    # ------------------------------------------------------------------
    # 2. Connect to Redis
    # ------------------------------------------------------------------
    redis_client = None
    try:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
        # Validate the connection is reachable
        await redis_client.ping()
        app.state.redis = redis_client
        _log({"event": "redis_connected", "url": settings.redis_url})
    except Exception as exc:  # noqa: BLE001
        _log({"event": "redis_connection_failed", "detail": str(exc)})
        health._startup_failure_reason = "redis_unreachable"
        # Close the (possibly partially initialised) client to avoid leaks
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass
        app.state.redis = None

    # ------------------------------------------------------------------
    # 3. Load embedding model
    # ------------------------------------------------------------------
    embedding_generator = EmbeddingGenerator(settings.embedding_model)
    try:
        embedding_generator.load()
        _log({"event": "embedding_model_loaded", "model": settings.embedding_model})
    except EmbeddingLoadError as exc:
        _log({"event": "embedding_load_failed", "model": settings.embedding_model, "detail": str(exc)})
        health._startup_failure_reason = "embedding_model_load_failed"
        # Continue — service still usable for exact-cache-only operation
    app.state.embedding_generator = embedding_generator

    # ------------------------------------------------------------------
    # 4. Instantiate ExactCacheService
    # ------------------------------------------------------------------
    app.state.exact_cache = ExactCacheService(app.state.redis)

    # ------------------------------------------------------------------
    # 5. Instantiate SemanticCacheService
    # ------------------------------------------------------------------
    app.state.semantic_cache = SemanticCacheService(app.state.redis, settings)

    # ------------------------------------------------------------------
    # 6. Mark service as ready
    # ------------------------------------------------------------------
    health._ready = True
    _log({"event": "startup_complete", "port": settings.port, "metrics_port": settings.port + 1})

    # ------------------------------------------------------------------
    # 7. Start Prometheus metrics server on port+1 as a background task
    # ------------------------------------------------------------------
    metrics_app = make_asgi_app()
    metrics_config = uvicorn.Config(
        app=metrics_app,
        host="0.0.0.0",
        port=settings.port + 1,
        log_level="warning",
    )
    metrics_server = uvicorn.Server(metrics_config)
    metrics_task = asyncio.create_task(metrics_server.serve())

    # ------------------------------------------------------------------
    # Yield — service is running
    # ------------------------------------------------------------------
    yield

    # ------------------------------------------------------------------
    # 8. Shutdown
    # ------------------------------------------------------------------
    _log({"event": "shutdown_initiated"})

    # Cancel metrics server task
    metrics_task.cancel()
    try:
        await metrics_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    # Close Redis connection
    if app.state.redis is not None:
        try:
            await app.state.redis.aclose()
            _log({"event": "redis_disconnected"})
        except Exception as exc:  # noqa: BLE001
            _log({"event": "redis_disconnect_failed", "detail": str(exc)})

    health._ready = False


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cache Service",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware — LoggingMiddleware wraps all requests
# ---------------------------------------------------------------------------

app.add_middleware(LoggingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(cache_router)

# ---------------------------------------------------------------------------
# Development entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _settings = get_settings()
    uvicorn.run(
        "cache_service.main:app",
        host="0.0.0.0",
        port=_settings.port,
    )
