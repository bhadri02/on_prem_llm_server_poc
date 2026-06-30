"""
routers/query.py — Query (read) endpoints for the Audit Store FastAPI service.

Provides:
  GET /audit/requests/{request_id}  — fetch all events for a request (HTTP 200)
  GET /audit/events                 — filtered event listing with optional params
  GET /audit/summary                — aggregated counts by outcome and layer
  GET /health                       — liveness / DB connectivity probe

All endpoints:
  - Do NOT require X-API-Key authentication (Req 10.6).
  - Access the shared SQLite connection via request.app.state.conn.
  - Use get_logger(__name__) for structured JSON logging.
  - Validate ISO-8601 UTC time params via the shared _validate_time_range helper.
"""

import concurrent.futures
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from audit_store.logging_config import get_logger
from audit_store.models import (
    AuditEventResponse,
    LayerEnum,
    OutcomeEnum,
    SummaryResponse,
    UUID4_RE,
)

logger = get_logger(__name__)

router = APIRouter()

# Expose the router under both names so main.py can import either.
query_router = router

# ---------------------------------------------------------------------------
# Internal helper — ISO-8601 UTC timestamp validation
# ---------------------------------------------------------------------------

def _parse_iso_utc(value: str) -> str:
    """Validate that *value* is an ISO-8601 datetime with a UTC suffix.

    Accepts a trailing ``Z`` or ``+00:00`` offset.  Returns the value
    unchanged on success so callers can pass it directly into a SQLite
    parameter tuple.

    Raises:
        ValueError: if the string lacks a UTC suffix or is not parseable.
    """
    from datetime import datetime, timezone

    v = value.strip()
    if not (v.endswith("Z") or v.endswith("+00:00")):
        raise ValueError(f"timestamp missing UTC suffix (Z or +00:00): {v!r}")

    # Normalise the Z suffix so fromisoformat can parse it (Python < 3.11).
    normalised = v.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalised)          # raises ValueError if malformed
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError(f"timestamp is not UTC: {v!r}")
    return v


def _validate_time_range(from_: str | None, to: str | None) -> None:
    """Parse and validate the optional *from_* / *to* query parameters.

    Raises :class:`fastapi.HTTPException` (422) when:
    - either value is present but not a valid ISO-8601 UTC datetime, or
    - both are present but *from_* is not strictly before *to*.

    Returns ``None`` on success.
    """
    from datetime import datetime
    from fastapi import HTTPException

    errors: dict[str, str] = {}
    from_dt = None
    to_dt = None

    if from_ is not None:
        try:
            _parse_iso_utc(from_)
            from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00"))
        except ValueError as exc:
            errors["from"] = (
                f"must be ISO-8601 UTC (e.g. 2024-01-01T00:00:00Z): {exc}"
            )

    if to is not None:
        try:
            _parse_iso_utc(to)
            to_dt = datetime.fromisoformat(to.replace("Z", "+00:00"))
        except ValueError as exc:
            errors["to"] = (
                f"must be ISO-8601 UTC (e.g. 2024-01-01T00:00:00Z): {exc}"
            )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid time parameter(s)", "errors": errors},
        )

    if from_dt is not None and to_dt is not None and from_dt >= to_dt:
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid time range", "from": from_, "to": to},
        )


# ---------------------------------------------------------------------------
# Internal helper — row → AuditEventResponse
# ---------------------------------------------------------------------------

def _row_to_response(row: sqlite3.Row) -> AuditEventResponse:
    """Convert a SQLite :class:`sqlite3.Row` into an :class:`AuditEventResponse`.

    Deserialises the ``pii_actions`` and ``policy_decisions`` TEXT columns
    back to native Python lists.  On JSON parse failure the raw string is
    kept and a WARNING is logged (Req 3.5 / 7.3).
    """
    audit_id = row["audit_id"]

    def _parse_json_col(col_name: str) -> Any:
        raw: str | None = row[col_name]
        if raw is None:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "json_deserialise_failed",
                extra={
                    "extra_fields": {
                        "audit_id": audit_id,
                        "column": col_name,
                        "raw_value": raw,
                    }
                },
            )
            return raw  # return raw string per spec (Req 3.5)

    return AuditEventResponse(
        audit_id=audit_id,
        request_id=row["request_id"],
        timestamp_utc=row["timestamp_utc"],
        user_id=row["user_id"],
        department=row["department"],
        layer=row["layer"],
        event_type=row["event_type"],
        model_used=row["model_used"],
        prompt_tokens=row["prompt_tokens"] or 0,
        completion_tokens=row["completion_tokens"] or 0,
        latency_ms=row["latency_ms"] or 0,
        outcome=row["outcome"],
        error_code=row["error_code"],
        pii_actions=_parse_json_col("pii_actions"),
        policy_decisions=_parse_json_col("policy_decisions"),
    )


# ---------------------------------------------------------------------------
# GET /audit/requests/{request_id}  (subtask 10.2)
# ---------------------------------------------------------------------------

@router.get("/audit/requests/{request_id}", response_model=list[AuditEventResponse])
async def get_events_by_request_id(request_id: str, request: Request):
    """Return all audit events for the given *request_id*, ordered by time.

    Validates that *request_id* is a UUID-v4; returns HTTP 422 if not.

    Satisfies: Req 3.1, 3.2, 3.3, 3.4, 3.5
    """
    from fastapi import HTTPException

    if not UUID4_RE.match(request_id):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "request_id must be a valid UUID-v4",
                "request_id": request_id,
            },
        )

    conn: sqlite3.Connection = request.app.state.conn
    cursor = conn.execute(
        "SELECT * FROM audit_events "
        "WHERE request_id = ? "
        "ORDER BY timestamp_utc ASC, audit_id ASC",
        (request_id,),
    )
    rows = cursor.fetchall()
    return [_row_to_response(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /audit/events  (subtask 10.3)
# ---------------------------------------------------------------------------

@router.get("/audit/events", response_model=list[AuditEventResponse])
async def get_events(
    request: Request,
    user_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
):
    """Return filtered audit events, capped at 1000 records, newest first.

    Satisfies: Req 4.1 – 4.10
    """
    from fastapi import HTTPException

    # Reject empty-string user_id (Req 4.10)
    if user_id is not None and user_id == "":
        raise HTTPException(
            status_code=422,
            detail={"message": "user_id must be a non-empty string"},
        )

    # Validate time range (raises 422 on failure)
    _validate_time_range(from_, to)

    # Build parameterised WHERE clause dynamically
    conditions: list[str] = []
    params: list[Any] = []

    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)

    if event_type is not None:
        conditions.append("event_type = ?")
        params.append(event_type)

    if from_ is not None:
        conditions.append("timestamp_utc >= ?")
        params.append(from_)

    if to is not None:
        conditions.append("timestamp_utc <= ?")
        params.append(to)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = (
        f"SELECT * FROM audit_events {where_clause} "
        "ORDER BY timestamp_utc DESC LIMIT 1000"
    )

    conn: sqlite3.Connection = request.app.state.conn
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    return [_row_to_response(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /audit/summary  (subtask 10.4)
# ---------------------------------------------------------------------------

@router.get("/audit/summary", response_model=SummaryResponse)
async def get_summary(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
):
    """Return aggregated event counts by outcome and layer.

    Satisfies: Req 5.1 – 5.7
    """
    # Validate time range (raises 422 on failure)
    _validate_time_range(from_, to)

    # Build shared WHERE clause
    conditions: list[str] = []
    params: list[Any] = []

    if from_ is not None:
        conditions.append("timestamp_utc >= ?")
        params.append(from_)

    if to is not None:
        conditions.append("timestamp_utc <= ?")
        params.append(to)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    conn: sqlite3.Connection = request.app.state.conn

    # Total count
    total_row = conn.execute(
        f"SELECT COUNT(*) FROM audit_events {where_clause}", params
    ).fetchone()
    total_events: int = total_row[0]

    # By outcome
    outcome_rows = conn.execute(
        f"SELECT outcome, COUNT(*) FROM audit_events {where_clause} GROUP BY outcome",
        params,
    ).fetchall()
    by_outcome: dict[str, int] = {row[0]: row[1] for row in outcome_rows if row[0]}

    # By layer
    layer_rows = conn.execute(
        f"SELECT layer, COUNT(*) FROM audit_events {where_clause} GROUP BY layer",
        params,
    ).fetchall()
    by_layer: dict[str, int] = {row[0]: row[1] for row in layer_rows if row[0]}

    return SummaryResponse(
        total_events=total_events,
        by_outcome=by_outcome,
        by_layer=by_layer,
    )


# ---------------------------------------------------------------------------
# GET /health  (subtask 10.5)
# ---------------------------------------------------------------------------

_HEALTH_TIMEOUT_S = 0.200  # 200 ms


def _db_probe(conn: sqlite3.Connection) -> None:
    """Execute SELECT 1 against the DB connection (run in a thread)."""
    conn.execute("SELECT 1")


@router.get("/health")
async def health(request: Request):
    """Kubernetes liveness probe — checks DB connectivity with a 200 ms timeout.

    Returns:
        HTTP 200  ``{"status": "ok",       "db": "connected"}``   on success
        HTTP 503  ``{"status": "degraded", "db": "unreachable"}``  on failure
    Satisfies: Req 6.1, 6.2, 6.3, 6.4
    """
    conn: sqlite3.Connection = request.app.state.conn

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_db_probe, conn)
            future.result(timeout=_HEALTH_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        logger.warning("health_check_timeout", extra={"extra_fields": {"db": "unreachable"}})
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unreachable"},
        )
    except Exception as exc:
        logger.warning(
            "health_check_failed",
            extra={"extra_fields": {"db": "unreachable", "error": str(exc)}},
        )
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unreachable"},
        )

    return {"status": "ok", "db": "connected"}
