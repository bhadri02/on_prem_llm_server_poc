"""
tests/audit_store_test_utils.py

Shared helper for building a fresh in-memory Audit Store FastAPI app for a
single test/Hypothesis example. Used by test_write_properties.py,
test_query_properties.py, test_logging_properties.py, and
test_governance_summary.py — all of which previously duplicated this
SQLite-connection setup inline; now they share one SQLAlchemy-engine-based
version (audit_store itself is Postgres in production — see
audit_store/database.py — but tests run against sqlite:///:memory: for
speed and zero external dependencies, using the same Core table).
"""

from contextlib import asynccontextmanager

from audit_store.database import create_db_executor, get_engine, init_schema
from audit_store.main import create_app

AUDIT_API_KEY = "test-key"


def make_audit_store_app():
    """Build a fresh in-memory FastAPI app + engine for one test example."""

    @asynccontextmanager
    async def _noop_lifespan(application):
        yield

    application = create_app()
    application.router.lifespan_context = _noop_lifespan

    engine = get_engine("sqlite:///:memory:")
    init_schema(engine)

    class _TestSettings:
        audit_api_key: str = AUDIT_API_KEY
        database_url: str = "sqlite:///:memory:"
        retention_days: int = 0

    application.state.engine = engine
    application.state.settings = _TestSettings()
    application.state.db_executor = create_db_executor()
    return application, engine
