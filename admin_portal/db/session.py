"""
admin_portal/db/session.py

SQLAlchemy engine/session wiring for the users/roles/API-keys store.
`get_db` is a FastAPI dependency yielding one Session per request.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from admin_portal.config import settings

_connect_args: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # Needed for SQLite when the same connection is shared across the
    # request/response cycle (used by tests, not production Postgres).
    _connect_args = {"check_same_thread": False}
elif settings.DATABASE_URL.startswith("postgresql"):
    # Fail reasonably fast instead of hanging on the OS-level TCP timeout
    # (which can be 20-30s) when Postgres is genuinely unreachable — callers
    # like routers/metrics_summary.py treat a DB failure as "return null",
    # and that only degrades gracefully if the failure itself doesn't also
    # hang the request. See DATABASE_CONNECT_TIMEOUT_SECONDS's docstring for
    # why this isn't set aggressively low by default.
    _connect_args = {"connect_timeout": settings.DATABASE_CONNECT_TIMEOUT_SECONDS}

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
