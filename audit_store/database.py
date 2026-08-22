"""
database.py — SQLAlchemy engine, schema, and DB-thread plumbing for the
Audit Store.

Provides:
  - metadata / audit_events: the Core table definition (portable across
    SQLite, used only in tests, and Postgres, the real runtime backend).
  - get_engine: builds a SQLAlchemy Engine from a DATABASE_URL.
  - init_schema: creates the audit_events table and its indexes.
  - create_db_executor / run_db: run blocking DB calls off the asyncio
    event loop, serialized on a single dedicated thread.
  - purge_older_than: deletes audit_events rows older than a cutoff
    timestamp (retention policy — see main.py's _retention_loop).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from sqlalchemy import (
    Column,
    Engine,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
)
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Schema — Core Table (not declarative ORM) since every caller here just
# wants rows in/out, not mapped objects. timestamp_utc stays a String
# column (not a native TIMESTAMP) so the exact ISO-8601 string every writer
# already formats and every query filter already compares lexicographically
# round-trips unchanged — ISO-8601 sorts correctly as text, so this loses
# nothing on either SQLite or Postgres.
# ---------------------------------------------------------------------------
metadata = MetaData()

audit_events = Table(
    "audit_events",
    metadata,
    Column("audit_id", String(36), primary_key=True),
    Column("request_id", String(36), nullable=False),
    Column("timestamp_utc", String(40), nullable=False),
    Column("user_id", String(255)),
    Column("department", String(255)),
    Column("layer", String(50)),
    Column("event_type", String(50)),
    Column("model_used", String(255)),
    Column("prompt_tokens", Integer, default=0),
    Column("completion_tokens", Integer, default=0),
    Column("latency_ms", Integer, default=0),
    Column("outcome", String(20)),
    Column("error_code", String(50)),
    Column("pii_actions", JSON),
    Column("policy_decisions", JSON),
    Index("idx_request_id", "request_id"),
    Index("idx_user_id", "user_id"),
    Index("idx_timestamp", "timestamp_utc"),
)


def get_engine(database_url: str) -> Engine:
    """Build a SQLAlchemy Engine for *database_url*.

    Production always passes a ``postgresql://`` URL; tests pass a
    ``sqlite://`` URL (typically ``sqlite:///:memory:``) for a fast,
    dependency-free run against the same Core table definition.
    """
    connect_args: dict = {}
    extra_kwargs: dict = {}
    if database_url.startswith("sqlite"):
        # Needed so the same connection is shared across the dedicated DB
        # thread and, in tests, the calling thread.
        connect_args = {"check_same_thread": False}
        if ":memory:" in database_url:
            # A plain sqlite3 in-memory DB is private to the connection that
            # created it — SQLAlchemy's default pool opens a fresh connection
            # (and therefore a fresh, empty database) per checkout. StaticPool
            # pins the engine to exactly one underlying connection so every
            # caller sees the same in-memory database.
            extra_kwargs["poolclass"] = StaticPool
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args, **extra_kwargs)


def create_db_executor() -> ThreadPoolExecutor:
    """
    Build the single-worker executor all DB access is serialized on.

    A single dedicated thread keeps SQLite's check_same_thread=False
    connection safe to touch from only one thread at a time, and — for
    Postgres — keeps psycopg2's blocking I/O off the asyncio event loop
    without introducing connection-pool concurrency to reason about in this
    POC-scale service.
    """
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-db")


async def run_db(executor: ThreadPoolExecutor, fn: Callable[[], T]) -> T:
    """Run a synchronous DB function on the dedicated single-worker executor.

    `fn` should perform one complete unit of work (e.g. an INSERT + commit,
    or a SELECT + fetch) so the whole operation runs atomically on that one
    thread. Exceptions raised inside `fn` propagate to the caller unchanged.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, fn)


def get_db_executor(app) -> ThreadPoolExecutor:
    """Return app.state.db_executor, lazily creating it if absent.

    The real lifespan (main.py) always sets this before serving traffic;
    the lazy-create fallback only matters for tests that build the app with
    a no-op lifespan and set app.state.engine/settings directly.
    """
    executor = getattr(app.state, "db_executor", None)
    if executor is None:
        executor = create_db_executor()
        app.state.db_executor = executor
    return executor


def init_schema(engine: Engine) -> None:
    """Create the ``audit_events`` table and its indexes if they do not exist.

    ``metadata.create_all`` only creates what's missing, making this
    function fully idempotent — safe to call on every startup.
    """
    metadata.create_all(engine)


def purge_older_than(engine: Engine, cutoff_iso: str) -> int:
    """Delete every audit_events row with timestamp_utc < cutoff_iso.

    Uses the same idx_timestamp index the query endpoints already rely on,
    so this is an indexed range delete, not a full table scan. Callers
    should route this through run_db() (see main.py's _retention_loop)
    rather than calling it directly from the event loop.

    Args:
        engine: A SQLAlchemy Engine.
        cutoff_iso: ISO-8601 UTC timestamp string (e.g. "2026-05-01T00:00:00Z")
                    — rows strictly older than this are deleted.

    Returns:
        The number of rows deleted.
    """
    with engine.begin() as conn:
        result = conn.execute(delete(audit_events).where(audit_events.c.timestamp_utc < cutoff_iso))
        return result.rowcount
