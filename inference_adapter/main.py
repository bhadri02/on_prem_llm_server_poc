"""
Application factory for the Inference Adapter (Layer 5).

Wires together the FastAPI app, async lifespan context manager, middleware
(LoggingMiddleware), and routers (health_router, infer_router).  Starts a
secondary uvicorn server on ``settings.metrics_port`` (default 9090) to serve
Prometheus metrics via ``prometheus_client.make_asgi_app()``, isolated from
the application port 8087.

Startup sequence (inside lifespan):
  1. Load settings (fail fast on invalid PORT, OLLAMA_TIMEOUT_SECONDS,
     DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS > MAX_TOKENS_LIMIT).
  2. Instantiate OllamaClient(base_url, timeout); store on app.state.ollama_client.
  3. Attempt GET /api/tags to populate app.state.ollama_models (list[str]).
     - On failure: log structured JSON warning, set app.state.ollama_reachable=False,
       app.state.ollama_models=[]; do NOT exit (degraded mode).
     - On success: set app.state.ollama_reachable=True.
  4. Set health._startup_complete = True (enables /health to report real state).
  5. Start Prometheus metrics server on settings.metrics_port as a background
     asyncio task. If the port cannot be bound (OSError): emit structured JSON
     error log and raise to fail startup (Requirement 11.6).
  6. yield — service is running.

Shutdown:
  - await app.state.ollama_client.close()
  - Cancel and await the metrics task.
  - Set health._startup_complete = False.

Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 11.6
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

import inference_adapter.routers.health as health_module
from inference_adapter.config import get_settings
from inference_adapter.middleware.logging import LoggingMiddleware
from inference_adapter.routers.health import health_router
from inference_adapter.routers.infer import infer_router
from inference_adapter.services.ollama_client import OllamaError


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
# Metrics server port-binding check
# ---------------------------------------------------------------------------

async def _start_metrics_server(metrics_port: int) -> asyncio.Task:
    """
    Start the Prometheus metrics server on *metrics_port* as a background task.

    Raises OSError if the port cannot be bound (Requirement 11.6), which
    propagates up to the lifespan startup and causes the process to fail.
    """
    metrics_app = make_asgi_app()
    metrics_config = uvicorn.Config(
        app=metrics_app,
        host="0.0.0.0",
        port=metrics_port,
        log_level="warning",
    )
    metrics_server = uvicorn.Server(metrics_config)

    # Probe port availability before starting the background task so we can
    # surface an OSError synchronously inside the lifespan startup block.
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", metrics_port))
    except OSError as exc:
        probe.close()
        raise OSError(
            f"Metrics port {metrics_port} cannot be bound: {exc}"
        ) from exc
    finally:
        probe.close()

    task = asyncio.create_task(metrics_server.serve())
    return task


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async context manager managing Inference Adapter startup and shutdown.

    Startup failures from invalid configuration (PORT, OLLAMA_TIMEOUT_SECONDS
    out of range, DEFAULT_MAX_TOKENS > MAX_TOKENS_LIMIT) propagate immediately
    as ValidationError and crash the process — this is intentional fail-fast
    behaviour (Requirements 13.4, 13.5).

    Ollama being unreachable at startup does NOT crash the process; the adapter
    starts in degraded mode (Requirement 13.1).

    If the metrics port cannot be bound the process fails to start
    (Requirement 11.6).
    """
    # ------------------------------------------------------------------
    # 1. Load settings — fail fast on ValidationError
    # ------------------------------------------------------------------
    settings = get_settings()

    # ------------------------------------------------------------------
    # 2. Instantiate OllamaClient and store on app.state
    # ------------------------------------------------------------------
    from inference_adapter.services.ollama_client import OllamaClient  # local to avoid circular

    ollama_client = OllamaClient(
        base_url=settings.ollama_base_url,
        timeout=float(settings.ollama_timeout_seconds),
    )
    app.state.ollama_client = ollama_client

    # ------------------------------------------------------------------
    # 3. Attempt initial model list — degraded mode on failure
    # ------------------------------------------------------------------
    try:
        models: list[str] = await ollama_client.list_models()
        app.state.ollama_models = models
        app.state.ollama_reachable = True
        _log(
            {
                "event": "ollama_connected_at_startup",
                "models": models,
            }
        )
    except Exception as exc:  # noqa: BLE001  — OllamaError or network failure
        _log(
            {
                "event": "ollama_unreachable_at_startup",
                "detail": str(exc),
            }
        )
        app.state.ollama_reachable = False
        app.state.ollama_models = []

    # ------------------------------------------------------------------
    # 4. Mark startup complete — /health can now report real state
    # ------------------------------------------------------------------
    health_module._startup_complete = True
    _log(
        {
            "event": "startup_complete",
            "port": settings.port,
            "metrics_port": settings.metrics_port,
            "ollama_reachable": app.state.ollama_reachable,
        }
    )

    # ------------------------------------------------------------------
    # 5. Start Prometheus metrics server on metrics_port
    #    Fail startup if port cannot be bound (Requirement 11.6)
    # ------------------------------------------------------------------
    metrics_task: asyncio.Task | None = None
    try:
        metrics_task = await _start_metrics_server(settings.metrics_port)
    except OSError as exc:
        _log(
            {
                "event": "metrics_port_bind_failed",
                "metrics_port": settings.metrics_port,
                "detail": str(exc),
            }
        )
        # Close the OllamaClient before raising so we don't leak resources
        try:
            await ollama_client.close()
        except Exception:  # noqa: BLE001
            pass
        health_module._startup_complete = False
        raise

    # ------------------------------------------------------------------
    # Yield — service is running
    # ------------------------------------------------------------------
    yield

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    _log({"event": "shutdown_initiated"})

    # Close the OllamaClient (closes the underlying httpx.AsyncClient)
    try:
        await app.state.ollama_client.close()
        _log({"event": "ollama_client_closed"})
    except Exception as exc:  # noqa: BLE001
        _log({"event": "ollama_client_close_failed", "detail": str(exc)})

    # Cancel the metrics background task
    if metrics_task is not None:
        metrics_task.cancel()
        try:
            await metrics_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # Reset startup flag so a restart cycle starts cleanly
    health_module._startup_complete = False


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Inference Adapter",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware — must be added before including routers
# ---------------------------------------------------------------------------

app.add_middleware(LoggingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(infer_router)

# ---------------------------------------------------------------------------
# Development entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _settings = get_settings()
    uvicorn.run(
        "inference_adapter.main:app",
        host="0.0.0.0",
        port=_settings.port,
    )
