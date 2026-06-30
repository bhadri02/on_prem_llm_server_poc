"""
database.py — SQLite connection and schema initialisation for the Audit Store.

Provides:
  - get_connection: Opens (or creates) the SQLite DB file and applies PRAGMAs.
  - init_schema: Creates the audit_events table and supporting indexes.
"""

import pathlib
import sqlite3


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *db_path*.

    For any path other than ``:memory:``, the parent directory must already
    exist.  If it does not, a descriptive :class:`FileNotFoundError` is raised
    *before* SQLite has a chance to create any file.

    After opening the connection the following PRAGMAs are applied:
      - ``journal_mode=WAL``  — concurrent readers don't block the writer
      - ``foreign_keys=ON``   — enforce FK constraints

    ``row_factory`` is set to :data:`sqlite3.Row` so that query results can be
    accessed by column name.

    Args:
        db_path: Filesystem path to the SQLite database file, or ``:memory:``
                 for an in-process in-memory database.

    Returns:
        An open :class:`sqlite3.Connection` ready for use.

    Raises:
        FileNotFoundError: If the parent directory of *db_path* does not exist
                           (skipped for ``:memory:``).
    """
    if db_path != ":memory:":
        parent = pathlib.Path(db_path).parent
        if not parent.exists():
            raise FileNotFoundError(
                f"Parent directory for DB does not exist: {parent}"
            )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``audit_events`` table and its indexes if they do not exist.

    The DDL uses ``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT
    EXISTS``, making this function fully idempotent — it is safe to call
    multiple times on the same connection.

    Schema
    ------
    audit_events columns:
      audit_id          TEXT PRIMARY KEY
      request_id        TEXT NOT NULL
      timestamp_utc     TEXT NOT NULL
      user_id           TEXT
      department        TEXT
      layer             TEXT
      event_type        TEXT
      model_used        TEXT
      prompt_tokens     INTEGER DEFAULT 0
      completion_tokens INTEGER DEFAULT 0
      latency_ms        INTEGER DEFAULT 0
      outcome           TEXT
      error_code        TEXT
      pii_actions       TEXT        (JSON array, stored as text)
      policy_decisions  TEXT        (JSON array, stored as text)

    Indexes:
      idx_request_id  on audit_events(request_id)
      idx_user_id     on audit_events(user_id)
      idx_timestamp   on audit_events(timestamp_utc)

    Args:
        conn: An open :class:`sqlite3.Connection` (typically from
              :func:`get_connection`).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_events (
            audit_id          TEXT PRIMARY KEY,
            request_id        TEXT NOT NULL,
            timestamp_utc     TEXT NOT NULL,
            user_id           TEXT,
            department        TEXT,
            layer             TEXT,
            event_type        TEXT,
            model_used        TEXT,
            prompt_tokens     INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            latency_ms        INTEGER DEFAULT 0,
            outcome           TEXT,
            error_code        TEXT,
            pii_actions       TEXT,
            policy_decisions  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_request_id ON audit_events(request_id);
        CREATE INDEX IF NOT EXISTS idx_user_id    ON audit_events(user_id);
        CREATE INDEX IF NOT EXISTS idx_timestamp  ON audit_events(timestamp_utc);
    """)
    conn.commit()
