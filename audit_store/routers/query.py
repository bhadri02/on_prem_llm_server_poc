"""
routers/query.py — Query (read) endpoints for the Audit Store FastAPI service.

Provides:
  GET /audit/requests/{request_id}  — fetch all events for a request (HTTP 200)
  GET /audit/events                 — filtered event listing with optional params
  GET /audit/summary                — aggregated counts by outcome and layer
  GET /audit/governance/summary     — AI governance/security/usage summary
  GET /health                       — liveness / DB connectivity probe

All endpoints:
  - Do NOT require X-API-Key authentication (Req 10.6).
  - Access the shared SQLAlchemy engine via request.app.state.engine.
  - Use get_logger(__name__) for structured JSON logging.
  - Validate ISO-8601 UTC time params via the shared _validate_time_range helper.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Row, func, select

from audit_store.database import audit_events, get_db_executor, run_db
from audit_store.logging_config import get_logger
from audit_store.models import (
    AuditEventResponse,
    GovernanceSummaryResponse,
    SummaryResponse,
    TokenUsage,
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
    unchanged on success so callers can pass it directly into a query filter.

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
# Internal helper — Row → AuditEventResponse
# ---------------------------------------------------------------------------

def _row_to_response(row: Row) -> AuditEventResponse:
    """Convert a SQLAlchemy Row into an :class:`AuditEventResponse`.

    pii_actions/policy_decisions are native JSON columns, so they arrive
    already deserialised as Python lists (``None`` for a never-written row,
    normalised to ``[]``).
    """
    m = row._mapping
    return AuditEventResponse(
        audit_id=m["audit_id"],
        request_id=m["request_id"],
        timestamp_utc=m["timestamp_utc"],
        user_id=m["user_id"],
        department=m["department"],
        layer=m["layer"],
        event_type=m["event_type"],
        model_used=m["model_used"],
        prompt_tokens=m["prompt_tokens"] or 0,
        completion_tokens=m["completion_tokens"] or 0,
        latency_ms=m["latency_ms"] or 0,
        outcome=m["outcome"],
        error_code=m["error_code"],
        pii_actions=m["pii_actions"] or [],
        policy_decisions=m["policy_decisions"] or [],
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

    engine = request.app.state.engine

    def _query() -> list[Row]:
        stmt = (
            select(audit_events)
            .where(audit_events.c.request_id == request_id)
            .order_by(audit_events.c.timestamp_utc.asc(), audit_events.c.audit_id.asc())
        )
        with engine.connect() as conn:
            return conn.execute(stmt).fetchall()

    rows = await run_db(get_db_executor(request.app), _query)
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

    conditions = []
    if user_id is not None:
        conditions.append(audit_events.c.user_id == user_id)
    if event_type is not None:
        conditions.append(audit_events.c.event_type == event_type)
    if from_ is not None:
        conditions.append(audit_events.c.timestamp_utc >= from_)
    if to is not None:
        conditions.append(audit_events.c.timestamp_utc <= to)

    stmt = select(audit_events).where(*conditions).order_by(audit_events.c.timestamp_utc.desc()).limit(1000)

    engine = request.app.state.engine

    def _query() -> list[Row]:
        with engine.connect() as conn:
            return conn.execute(stmt).fetchall()

    rows = await run_db(get_db_executor(request.app), _query)
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
    _validate_time_range(from_, to)

    conditions = []
    if from_ is not None:
        conditions.append(audit_events.c.timestamp_utc >= from_)
    if to is not None:
        conditions.append(audit_events.c.timestamp_utc <= to)

    engine = request.app.state.engine

    def _query() -> tuple[int, list, list]:
        with engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(audit_events).where(*conditions)
            ).scalar_one()
            outcome_rows = conn.execute(
                select(audit_events.c.outcome, func.count())
                .where(*conditions)
                .group_by(audit_events.c.outcome)
            ).fetchall()
            layer_rows = conn.execute(
                select(audit_events.c.layer, func.count())
                .where(*conditions)
                .group_by(audit_events.c.layer)
            ).fetchall()
        return total, outcome_rows, layer_rows

    total_events, outcome_rows, layer_rows = await run_db(get_db_executor(request.app), _query)
    by_outcome: dict[str, int] = {row[0]: row[1] for row in outcome_rows if row[0]}
    by_layer: dict[str, int] = {row[0]: row[1] for row in layer_rows if row[0]}

    return SummaryResponse(
        total_events=total_events,
        by_outcome=by_outcome,
        by_layer=by_layer,
    )


# ---------------------------------------------------------------------------
# GET /audit/governance/summary
# ---------------------------------------------------------------------------

@router.get("/audit/governance/summary", response_model=GovernanceSummaryResponse)
async def get_governance_summary(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
):
    """Return AI governance / security / usage counts computed from the real
    audit trail — the durable, always-available complement to Prometheus-rate
    based metrics (which require a live Prometheus server to be useful).

    Aggregates, over the optional ``[from, to]`` window:
      - ``requests_blocked_total`` / ``blocked_by_reason`` — every row with
        ``outcome='block'`` (security_layer's injection/content-safety/policy
        blocks, and intelligent_router's policy_denied/model_not_entitled
        denials), grouped by ``error_code`` (falling back to ``event_type``
        for older rows written before ``error_code`` was populated on the
        security layer's block events).
      - ``injection_flagged_total`` — the ``injection_detected`` slice of the
        above (injection scoring is binary in this POC, so flagged and
        blocked are the same event).
      - ``pii_detections_total`` — sum of the lengths of every row's
        ``pii_actions`` JSON array (masked PII entities, request or response
        side).
      - ``token_usage`` — sum of ``prompt_tokens``/``completion_tokens``
        across all rows.
      - ``model_usage`` — count of successfully served requests
        (``layer='router'``, ``event_type`` in ``inference_complete`` /
        ``cache_hit``) grouped by ``model_used``.
    """
    _validate_time_range(from_, to)

    conditions = []
    if from_ is not None:
        conditions.append(audit_events.c.timestamp_utc >= from_)
    if to is not None:
        conditions.append(audit_events.c.timestamp_utc <= to)

    engine = request.app.state.engine
    block_conditions = [*conditions, audit_events.c.outcome == "block"]
    blocked_by_reason_col = func.coalesce(audit_events.c.error_code, audit_events.c.event_type)

    def _query() -> tuple[int, list, list, list, list]:
        with engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(audit_events).where(*conditions)
            ).scalar_one()
            outcome_rows = conn.execute(
                select(audit_events.c.outcome, func.count())
                .where(*conditions)
                .group_by(audit_events.c.outcome)
            ).fetchall()
            layer_rows = conn.execute(
                select(audit_events.c.layer, func.count())
                .where(*conditions)
                .group_by(audit_events.c.layer)
            ).fetchall()
            blocked_rows = conn.execute(
                select(blocked_by_reason_col, func.count())
                .where(*block_conditions)
                .group_by(blocked_by_reason_col)
            ).fetchall()
            # Token totals, PII entity counts, and per-model usage all require
            # per-row inspection (JSON column length, event_type+layer
            # filtering) rather than a single GROUP BY — fetch once and fold
            # in Python. POC audit-trail scale makes this cheap; revisit if
            # the table grows large.
            detail_rows = conn.execute(
                select(
                    audit_events.c.prompt_tokens,
                    audit_events.c.completion_tokens,
                    audit_events.c.pii_actions,
                    audit_events.c.layer,
                    audit_events.c.event_type,
                    audit_events.c.model_used,
                ).where(*conditions)
            ).fetchall()
        return total, outcome_rows, layer_rows, blocked_rows, detail_rows

    total_events, outcome_rows, layer_rows, blocked_rows, rows = await run_db(
        get_db_executor(request.app), _query
    )

    by_outcome: dict[str, int] = {row[0]: row[1] for row in outcome_rows if row[0]}
    by_layer: dict[str, int] = {row[0]: row[1] for row in layer_rows if row[0]}
    blocked_by_reason: dict[str, int] = {row[0]: row[1] for row in blocked_rows if row[0]}
    requests_blocked_total = sum(blocked_by_reason.values())
    injection_flagged_total = blocked_by_reason.get("injection_detected", 0)

    prompt_tokens_total = 0
    completion_tokens_total = 0
    pii_detections_total = 0
    model_usage: dict[str, int] = {}

    for row in rows:
        m = row._mapping
        prompt_tokens_total += m["prompt_tokens"] or 0
        completion_tokens_total += m["completion_tokens"] or 0

        pii_actions = m["pii_actions"]
        if isinstance(pii_actions, list):
            pii_detections_total += len(pii_actions)

        if m["layer"] == "router" and m["event_type"] in ("inference_complete", "cache_hit") and m["model_used"]:
            model_usage[m["model_used"]] = model_usage.get(m["model_used"], 0) + 1

    return GovernanceSummaryResponse(
        total_events=total_events,
        by_outcome=by_outcome,
        by_layer=by_layer,
        requests_blocked_total=requests_blocked_total,
        blocked_by_reason=blocked_by_reason,
        injection_flagged_total=injection_flagged_total,
        pii_detections_total=pii_detections_total,
        token_usage=TokenUsage(
            prompt_tokens=prompt_tokens_total,
            completion_tokens=completion_tokens_total,
            total_tokens=prompt_tokens_total + completion_tokens_total,
        ),
        model_usage=model_usage,
    )


# ---------------------------------------------------------------------------
# GET /health  (subtask 10.5)
# ---------------------------------------------------------------------------

_HEALTH_TIMEOUT_S = 0.200  # 200 ms


@router.get("/health")
async def health(request: Request):
    """Kubernetes liveness probe — checks DB connectivity with a 200 ms timeout.

    Returns:
        HTTP 200  ``{"status": "ok",       "db": "connected"}``   on success
        HTTP 503  ``{"status": "degraded", "db": "unreachable"}``  on failure
    Satisfies: Req 6.1, 6.2, 6.3, 6.4
    """
    engine = request.app.state.engine

    def _probe() -> None:
        with engine.connect() as conn:
            conn.execute(select(1))

    try:
        # Runs on the same dedicated single-worker executor as every other
        # DB call (not a throwaway executor of its own) — a timeout here
        # correctly reflects a saturated/blocked DB, not just an unreachable
        # one.
        await asyncio.wait_for(
            run_db(get_db_executor(request.app), _probe), timeout=_HEALTH_TIMEOUT_S
        )
    except asyncio.TimeoutError:
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
