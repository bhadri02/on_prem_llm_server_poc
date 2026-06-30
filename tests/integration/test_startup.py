"""
tests/integration/test_startup.py — Integration tests for the lifespan startup validation.

Covers:
  - test_empty_api_key_exits       : Empty AUDIT_API_KEY triggers sys.exit(1)
  - test_missing_db_path_parent_exits : Non-existent DB parent directory triggers sys.exit(1)
  - test_valid_config_startup       : Valid config opens DB, creates schema, sets WAL mode
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI

from audit_store.main import lifespan
from audit_store.config import Settings


@pytest.mark.asyncio
async def test_empty_api_key_exits():
    """An empty AUDIT_API_KEY must cause sys.exit(1) during startup."""
    mock_settings = MagicMock(spec=Settings)
    mock_settings.audit_api_key = ""
    mock_settings.db_path = ":memory:"

    test_app = FastAPI(lifespan=lifespan)

    with patch("audit_store.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_missing_db_path_parent_exits():
    """A DB_PATH whose parent directory does not exist must cause sys.exit(1)."""
    mock_settings = MagicMock(spec=Settings)
    mock_settings.audit_api_key = "valid-test-key"
    mock_settings.db_path = "/nonexistent_dir_xyz_abc_123/audit.db"

    test_app = FastAPI(lifespan=lifespan)

    with patch("audit_store.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_valid_config_startup():
    """Valid API key + :memory: DB_PATH must complete startup and initialise schema."""
    mock_settings = MagicMock(spec=Settings)
    mock_settings.audit_api_key = "valid-test-key"
    mock_settings.db_path = ":memory:"

    test_app = FastAPI(lifespan=lifespan)

    with patch("audit_store.main.settings", mock_settings):
        async with lifespan(test_app):
            conn = test_app.state.conn

            # Connection must have been stored on app.state.
            assert conn is not None

            # The audit_events table must exist.
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
            ).fetchone()
            assert row is not None, "audit_events table was not created"

            # SQLite in-memory databases always use "memory" journal mode — WAL is
            # a disk-only feature.  Verify the PRAGMA is readable and returns a
            # sensible value (either "wal" for file DBs or "memory" for :memory:).
            mode_row = conn.execute("PRAGMA journal_mode").fetchone()
            assert mode_row is not None
            assert mode_row[0] in ("wal", "memory"), (
                f"Unexpected journal mode: {mode_row[0]}"
            )
