"""
main.py — FastAPI app factory and lifespan for the Audit Store service.
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from audit_store.auth import APIKeyMiddleware
from audit_store.config import settings
from audit_store.database import create_db_executor, get_engine, init_schema, purge_older_than, run_db
from audit_store.logging_config import get_logger
from audit_store.routers.write import router as write_router
from audit_store.routers.query import router as query_router

logger = get_logger(__name__)


async def _retention_loop(app: FastAPI) -> None:
    """Background loop: periodically purge audit_events older than
    settings.retention_days. No-ops entirely when retention_days <= 0
    (the default) — see config.py's docstring for why that's the default.

    Runs once at startup (after a short delay to let the app finish
    starting) and then every retention_check_interval_seconds. Purge
    failures are logged and never crash the service or stop the loop.
    """
    if settings.retention_days <= 0:
        return

    await asyncio.sleep(5)  # let startup finish before the first run
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            deleted = await run_db(
                app.state.db_executor,
                lambda: purge_older_than(app.state.engine, cutoff_iso),
            )
            if deleted:
                logger.info(
                    "audit_retention_purge",
                    extra={"extra_fields": {"deleted_rows": deleted, "cutoff": cutoff_iso}},
                )
        except Exception as exc:  # noqa: BLE001 — never let this loop die
            logger.error(
                "audit_retention_purge_failed",
                extra={"extra_fields": {"error": str(exc)}},
            )
        await asyncio.sleep(settings.retention_check_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager.

    Startup (before yield):
      1. Validates AUDIT_API_KEY is non-empty — exits with code 1 if not.
      2. Builds the Postgres (or, in tests, SQLite) engine via get_engine.
      3. Initialises the schema via init_schema.
      4. Stores engine and settings on app.state for router access.

    Shutdown (after yield):
      - Disposes the engine's connection pool.

    Satisfies: Req 7.5, 7.7, 7.8, 10.4, 10.5
    """
    # --- startup validation ---

    # 1. AUDIT_API_KEY must be non-empty (Req 10.4 / 10.5)
    if not settings.audit_api_key:
        logger.error("AUDIT_API_KEY is not set or empty; refusing to start")
        sys.exit(1)

    # 2/3. Build the engine and initialise the schema (idempotent — safe on
    # every startup). A DB that's unreachable at boot (e.g. Postgres still
    # starting up) is a hard startup failure, same as the old SQLite-open
    # failure case.
    try:
        engine = get_engine(settings.database_url)
        init_schema(engine)
    except Exception as exc:
        logger.error(
            "Failed to connect to the audit database",
            extra={"extra_fields": {"error": str(exc)}},
        )
        sys.exit(1)

    # 4. Store on app.state so routers can access via request.app.state
    app.state.engine = engine
    app.state.settings = settings
    # All DB calls run on this single dedicated thread instead of blocking
    # the asyncio event loop directly (see database.run_db).
    app.state.db_executor = create_db_executor()

    retention_task = asyncio.create_task(_retention_loop(app))

    logger.info(
        "Audit Store started",
        extra={"extra_fields": {"retention_days": settings.retention_days}},
    )

    yield

    # --- shutdown ---
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass
    app.state.db_executor.shutdown(wait=True)
    engine.dispose()
    logger.info("Audit Store stopped")


def create_app() -> FastAPI:
    """Factory that creates and fully configures a FastAPI application instance.

    Intended for use by tests (each call returns a fresh instance for
    isolation) and by the module-level ``app`` used by uvicorn.

    Returns:
        A configured :class:`FastAPI` instance with lifespan, exception
        handlers, middleware, and routers attached.
    """
    application = FastAPI(
        title="Audit Store",
        description=(
            "Append-only audit trail service for the "
            "Enterprise On-Premises LLM Platform"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- Custom exception handler (subtask 11.3) ---
    # Pydantic v2 sets type="json_invalid" for JSON body parse failures.
    # Those must return HTTP 400 (Req 1.8); all other validation errors → 422.
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        is_json_parse_error = any(
            e.get("type") in ("json_invalid", "json_type") for e in errors
        )
        status_code = 400 if is_json_parse_error else 422

        # Pydantic v2 includes a ``ctx`` dict that may contain a raw Exception
        # object (e.g. ``{"error": ValueError(...)}``) which is not JSON-
        # serializable.  Sanitise by converting any exception to its string
        # representation.
        def _sanitise(error: dict) -> dict:
            sanitised = dict(error)
            if "ctx" in sanitised and isinstance(sanitised["ctx"], dict):
                sanitised["ctx"] = {
                    k: str(v) if isinstance(v, Exception) else v
                    for k, v in sanitised["ctx"].items()
                }
            # loc is a tuple of (str | int); ensure it is a plain list for JSON.
            if "loc" in sanitised:
                sanitised["loc"] = list(sanitised["loc"])
            return sanitised

        sanitised_errors = [_sanitise(e) for e in errors]
        return JSONResponse(
            status_code=status_code,
            content={"detail": sanitised_errors},
        )

    # --- Auth middleware (subtask 11.4) ---
    # Guards POST /audit/events and POST /audit/events/batch only.
    application.add_middleware(APIKeyMiddleware)

    # --- Routers (subtask 11.5) ---
    application.include_router(write_router, prefix="")
    application.include_router(query_router, prefix="")

    return application


# Module-level app instance used by uvicorn:
#   uvicorn audit_store.main:app --host 0.0.0.0 --port 9200
app = create_app()
