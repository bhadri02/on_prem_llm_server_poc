"""
admin_portal/db/migrations.py

`Base.metadata.create_all` only creates tables that don't exist yet — it
never adds columns to a table that's already there. The real Postgres DB
this project runs against already had a `users` table before Phase 6 added
`password_hash`, so that column needs an explicit, idempotent ALTER TABLE.
Not needed for SQLite test DBs, since those are always created fresh from
the current model definitions.
"""

from __future__ import annotations

from sqlalchemy import Engine, text


def run_additive_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
