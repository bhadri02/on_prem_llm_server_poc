"""
main.py — FastAPI app factory and lifespan for the Audit Store service.
"""
import sys
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from audit_store.auth import APIKeyMiddleware
from audit_store.config import settings
from audit_store.database import get_connection, init_schema
from audit_store.logging_config import get_logger
from audit_store.routers.write import router as write_router
from audit_store.routers.query import router as query_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager.

    Startup (before yield):
      1. Validates AUDIT_API_KEY is non-empty — exits with code 1 if not.
      2. Validates parent directory of DB_PATH exists — exits with code 1 if not.
         (Skipped for :memory: paths.)
      3. Opens the SQLite connection via get_connection.
      4. Initialises the schema via init_schema.
      5. Stores conn and settings on app.state for router access.

    Shutdown (after yield):
      - Closes the SQLite connection.

    Satisfies: Req 7.5, 7.7, 7.8, 10.4, 10.5
    """
    # --- startup validation ---

    # 1. AUDIT_API_KEY must be non-empty (Req 10.4 / 10.5)
    if not settings.audit_api_key:
        logger.error("AUDIT_API_KEY is not set or empty; refusing to start")
        sys.exit(1)

    # 2. Parent directory of DB_PATH must exist (Req 7.5) — skip for :memory:
    if settings.db_path != ":memory:":
        parent = pathlib.Path(settings.db_path).parent
        if not parent.exists():
            logger.error(
                "DB_PATH parent directory does not exist",
                extra={
                    "extra_fields": {
                        "db_path": settings.db_path,
                        "missing_dir": str(parent),
                    }
                },
            )
            sys.exit(1)

    # 3. Open SQLite connection (Req 7.7 / 7.8)
    try:
        conn = get_connection(settings.db_path)
    except Exception as exc:
        logger.error(
            "Failed to open SQLite connection",
            extra={
                "extra_fields": {
                    "error": str(exc),
                    "db_path": settings.db_path,
                }
            },
        )
        sys.exit(1)

    # 4. Initialise schema (idempotent — safe on every startup)
    init_schema(conn)

    # 5. Store on app.state so routers can access via request.app.state
    app.state.conn = conn
    app.state.settings = settings

    logger.info(
        "Audit Store started",
        extra={"extra_fields": {"db_path": settings.db_path}},
    )

    yield

    # --- shutdown ---
    conn.close()
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
