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

from admin_portal.db.models import DEFAULT_RATE_LIMIT_RPM


def run_additive_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))

        # api_keys.rate_limit_rpm used to be nullable (NULL meant "fall back
        # to a shared, gateway-wide default"). Rate limiting is now strictly
        # per-key with no platform-wide fallback, so every row needs a real
        # value. Backfill first, then tighten the column so future direct
        # inserts can't reintroduce NULLs.
        conn.execute(
            text(
                "UPDATE api_keys SET rate_limit_rpm = :default_rpm "
                "WHERE rate_limit_rpm IS NULL"
            ),
            {"default_rpm": DEFAULT_RATE_LIMIT_RPM},
        )
        conn.execute(
            text(
                f"ALTER TABLE api_keys ALTER COLUMN rate_limit_rpm SET DEFAULT {DEFAULT_RATE_LIMIT_RPM}"
            )
        )
        conn.execute(text("ALTER TABLE api_keys ALTER COLUMN rate_limit_rpm SET NOT NULL"))
