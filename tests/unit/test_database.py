"""
tests/unit/test_database.py

Unit tests for audit_store.database (SQLAlchemy-based — production runs
against Postgres, these tests run against sqlite:///:memory: for speed):
  - init_schema is idempotent (safe to call twice)
  - audit_events table and all three indexes exist after init_schema
  - purge_older_than deletes only rows strictly older than the cutoff
"""

from sqlalchemy import inspect

from audit_store.database import audit_events, get_engine, init_schema, purge_older_than


# ---------------------------------------------------------------------------
# init_schema is idempotent
# ---------------------------------------------------------------------------

class TestInitSchemaIdempotent:
    def test_calling_init_schema_twice_does_not_raise(self):
        """init_schema must not raise when called a second time on the same engine."""
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)   # first call
        init_schema(engine)   # second call — must not raise
        engine.dispose()

    def test_table_exists_after_second_call(self):
        """audit_events table must still be present after a second init_schema call."""
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)
        init_schema(engine)

        assert "audit_events" in inspect(engine).get_table_names()
        engine.dispose()

    def test_indexes_exist_after_second_call(self):
        """All indexes must still be present after a second init_schema call."""
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)
        init_schema(engine)

        names = {idx["name"] for idx in inspect(engine).get_indexes("audit_events")}
        for idx in ("idx_request_id", "idx_user_id", "idx_timestamp"):
            assert idx in names, f"Index {idx!r} missing after two init_schema calls"
        engine.dispose()


# ---------------------------------------------------------------------------
# All three indexes exist
# ---------------------------------------------------------------------------

class TestIndexesExist:
    def test_all_three_indexes_present(self):
        """idx_request_id, idx_user_id, and idx_timestamp must all exist after init_schema."""
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)

        names = {idx["name"] for idx in inspect(engine).get_indexes("audit_events")}
        assert "idx_request_id" in names, "idx_request_id index is missing"
        assert "idx_user_id" in names, "idx_user_id index is missing"
        assert "idx_timestamp" in names, "idx_timestamp index is missing"
        engine.dispose()

    def test_exactly_the_declared_indexes_exist(self):
        """Exactly the three declared indexes exist."""
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)

        names = {idx["name"] for idx in inspect(engine).get_indexes("audit_events")}
        expected = {"idx_request_id", "idx_user_id", "idx_timestamp"}
        assert names == expected, f"Expected declared indexes {expected}, got {names}"
        engine.dispose()


# ---------------------------------------------------------------------------
# purge_older_than — retention policy
# ---------------------------------------------------------------------------

def _insert_event(engine, audit_id: str, timestamp_utc: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            audit_events.insert().values(
                audit_id=audit_id,
                request_id="req-" + audit_id,
                timestamp_utc=timestamp_utc,
                outcome="success",
            )
        )


def _all_audit_ids(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(audit_events.select()).fetchall()
    return {row._mapping["audit_id"] for row in rows}


class TestPurgeOlderThan:
    def test_deletes_rows_older_than_cutoff(self):
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)
        _insert_event(engine, "old-1", "2020-01-01T00:00:00.000Z")
        _insert_event(engine, "old-2", "2020-06-01T00:00:00.000Z")
        _insert_event(engine, "new-1", "2030-01-01T00:00:00.000Z")

        deleted = purge_older_than(engine, "2025-01-01T00:00:00.000Z")

        assert deleted == 2
        assert _all_audit_ids(engine) == {"new-1"}
        engine.dispose()

    def test_does_not_delete_rows_at_or_after_cutoff(self):
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)
        _insert_event(engine, "exact-cutoff", "2025-01-01T00:00:00.000Z")
        _insert_event(engine, "after-cutoff", "2025-06-01T00:00:00.000Z")

        deleted = purge_older_than(engine, "2025-01-01T00:00:00.000Z")

        assert deleted == 0
        assert _all_audit_ids(engine) == {"exact-cutoff", "after-cutoff"}
        engine.dispose()

    def test_returns_zero_when_table_empty(self):
        engine = get_engine("sqlite:///:memory:")
        init_schema(engine)

        deleted = purge_older_than(engine, "2025-01-01T00:00:00.000Z")

        assert deleted == 0
        engine.dispose()
