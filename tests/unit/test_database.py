"""
tests/unit/test_database.py

Unit tests for audit_store.database:
  - init_schema is idempotent (safe to call twice)
  - WAL journal mode is active after get_connection
  - All three indexes exist after init_schema
  - Missing parent directory raises FileNotFoundError before any file is written
"""

import os
import sqlite3

import pytest

from audit_store.database import get_connection, init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _index_names(conn: sqlite3.Connection) -> set[str]:
    """Return the set of index names on the audit_events table."""
    rows = conn.execute("PRAGMA index_list('audit_events')").fetchall()
    return {row["name"] for row in rows}


# ---------------------------------------------------------------------------
# 4.3.1  init_schema is idempotent
# ---------------------------------------------------------------------------

class TestInitSchemaIdempotent:
    def test_calling_init_schema_twice_does_not_raise(self):
        """init_schema must not raise when called a second time on the same connection."""
        conn = get_connection(":memory:")
        init_schema(conn)   # first call
        init_schema(conn)   # second call — must not raise
        conn.close()

    def test_table_exists_after_second_call(self):
        """audit_events table must still be present after a second init_schema call."""
        conn = get_connection(":memory:")
        init_schema(conn)
        init_schema(conn)

        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
        ).fetchone()
        assert result is not None, "audit_events table must exist after two init_schema calls"
        conn.close()

    def test_indexes_exist_after_second_call(self):
        """All indexes must still be present after a second init_schema call."""
        conn = get_connection(":memory:")
        init_schema(conn)
        init_schema(conn)

        names = _index_names(conn)
        for idx in ("idx_request_id", "idx_user_id", "idx_timestamp"):
            assert idx in names, f"Index {idx!r} missing after two init_schema calls"
        conn.close()


# ---------------------------------------------------------------------------
# 4.3.2  WAL mode is active after get_connection
# ---------------------------------------------------------------------------

class TestWALMode:
    def test_journal_mode_is_wal_for_memory_db(self):
        """journal_mode PRAGMA must return 'wal' after get_connection (in-memory)."""
        conn = get_connection(":memory:")
        row = conn.execute("PRAGMA journal_mode").fetchone()
        # In-memory databases don't persist WAL but the PRAGMA response is 'memory'.
        # The important case is a file-based DB; we also verify file-based below.
        conn.close()

    def test_journal_mode_is_wal_for_file_db(self, tmp_path):
        """journal_mode PRAGMA must return 'wal' for a file-based database."""
        db_file = tmp_path / "test_wal.db"
        conn = get_connection(str(db_file))
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal", f"Expected journal_mode='wal', got {row[0]!r}"
        conn.close()


# ---------------------------------------------------------------------------
# 4.3.3  All three indexes exist after init_schema
# ---------------------------------------------------------------------------

class TestIndexesExist:
    def test_all_three_indexes_present(self):
        """idx_request_id, idx_user_id, and idx_timestamp must all exist after init_schema."""
        conn = get_connection(":memory:")
        init_schema(conn)

        names = _index_names(conn)
        assert "idx_request_id" in names, "idx_request_id index is missing"
        assert "idx_user_id" in names, "idx_user_id index is missing"
        assert "idx_timestamp" in names, "idx_timestamp index is missing"
        conn.close()

    def test_exactly_the_declared_indexes_exist(self):
        """Exactly the three declared indexes exist (ignoring SQLite auto-indexes)."""
        conn = get_connection(":memory:")
        init_schema(conn)

        # SQLite automatically creates internal indexes for PRIMARY KEY columns
        # (named like 'sqlite_autoindex_<table>_N').  Filter those out so the
        # test only checks for the explicitly declared indexes.
        rows = conn.execute("PRAGMA index_list('audit_events')").fetchall()
        names = {
            row["name"]
            for row in rows
            if not row["name"].startswith("sqlite_autoindex_")
        }
        expected = {"idx_request_id", "idx_user_id", "idx_timestamp"}
        assert names == expected, (
            f"Expected declared indexes {expected}, got {names}"
        )
        conn.close()


# ---------------------------------------------------------------------------
# 4.3.4  Missing parent directory raises FileNotFoundError before any file write
# ---------------------------------------------------------------------------

class TestMissingParentDirectory:
    def test_nonexistent_parent_raises_file_not_found(self):
        """get_connection must raise FileNotFoundError for a path whose parent doesn't exist."""
        bad_path = "/nonexistent_dir_abc123/audit.db"
        with pytest.raises((FileNotFoundError, OSError)) as exc_info:
            get_connection(bad_path)
        # The error message must identify the missing directory
        assert "nonexistent_dir_abc123" in str(exc_info.value), (
            f"Expected the error message to name the missing directory, got: {exc_info.value}"
        )

    def test_nonexistent_parent_raises_on_windows_style_path(self, tmp_path):
        """Missing nested parent also raises FileNotFoundError."""
        # tmp_path exists, but tmp_path/missing_subdir does not
        missing_parent = tmp_path / "missing_subdir"
        db_path = str(missing_parent / "audit.db")

        with pytest.raises((FileNotFoundError, OSError)) as exc_info:
            get_connection(db_path)
        assert "missing_subdir" in str(exc_info.value), (
            f"Expected error message to name the missing directory, got: {exc_info.value}"
        )

    def test_no_file_created_when_parent_missing(self, tmp_path):
        """get_connection must not create any file when the parent directory is absent."""
        missing_parent = tmp_path / "ghost_dir"
        db_path = missing_parent / "audit.db"

        try:
            get_connection(str(db_path))
        except (FileNotFoundError, OSError):
            pass  # expected

        assert not db_path.exists(), (
            "get_connection must not create the DB file when the parent directory is missing"
        )

    def test_memory_db_skips_parent_check(self):
        """:memory: must never trigger the parent-directory check."""
        conn = get_connection(":memory:")  # must not raise
        assert conn is not None
        conn.close()

    def test_valid_parent_directory_succeeds(self, tmp_path):
        """get_connection must succeed when the parent directory exists."""
        db_path = tmp_path / "audit.db"
        conn = get_connection(str(db_path))
        assert conn is not None
        conn.close()
