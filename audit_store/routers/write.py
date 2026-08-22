"""
routers/write.py — Write endpoints for the Audit Store FastAPI service.

Provides:
  POST /audit/events        — insert a single audit event (HTTP 201)
  POST /audit/events/batch  — insert a batch of audit events atomically (HTTP 201)

Both endpoints:
  - Measure total handler latency via time.monotonic() and record it in the
    write_latency histogram even when the write fails (Req 8.3 / 8.4).
  - Auto-generate audit_id (UUID-v4) and timestamp_utc when absent (Req 1.2 / 1.3).
  - Store pii_actions and policy_decisions as native JSON column values (Req 7.3).
  - Increment writes_total ONLY on confirmed successful inserts (Req 8.2).
  - Return HTTP 409 on duplicate audit_id (Req 7.4).
  - Return HTTP 500 + ERROR log on unexpected DB errors (Req 1.9 / 2.6).
  - Emit single-line JSON logs to stdout (Req 9.1 / 9.2 / 9.3).
"""

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from audit_store.database import audit_events, get_db_executor, run_db
from audit_store.logging_config import get_logger
from audit_store.metrics import write_latency, writes_total
from audit_store.models import (
    AuditEventCreate,
    AuditEventResponse,
    BatchWriteRequest,
    BatchWriteResponse,
)

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _row_values(event: AuditEventCreate, audit_id: str, timestamp_utc: str) -> dict:
    """Build the column/value mapping for one INSERT."""
    return {
        "audit_id": audit_id,
        "request_id": event.request_id,
        "timestamp_utc": timestamp_utc,
        "user_id": event.user_id,
        "department": event.department,
        "layer": event.layer.value,
        "event_type": event.event_type.value,
        "model_used": event.model_used,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "latency_ms": event.latency_ms,
        "outcome": event.outcome.value,
        "error_code": event.error_code,
        "pii_actions": event.pii_actions,
        "policy_decisions": event.policy_decisions,
    }


# ---------------------------------------------------------------------------
# POST /audit/events
# ---------------------------------------------------------------------------


@router.post("/audit/events", status_code=201)
async def create_audit_event(event: AuditEventCreate, request: Request):
    """Insert a single audit event and return the stored record (HTTP 201).

    Satisfies: Req 1.1, 1.2, 1.3, 1.9, 7.3, 7.4, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3
    """
    start = time.monotonic()

    # Resolve optional fields (subtask 9.1)
    audit_id = event.audit_id or str(uuid.uuid4())
    timestamp_utc = event.timestamp_utc or (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )

    layer_val = event.layer.value
    event_type_val = event.event_type.value

    engine = request.app.state.engine

    def _write() -> None:
        with engine.begin() as conn:
            conn.execute(audit_events.insert().values(**_row_values(event, audit_id, timestamp_utc)))

    try:
        await run_db(get_db_executor(request.app), _write)

    except IntegrityError:
        # Duplicate audit_id — the transaction rolled back automatically.
        elapsed = time.monotonic() - start
        write_latency.labels(event_type=event_type_val, layer=layer_val).observe(elapsed)
        return JSONResponse(
            status_code=409,
            content={"error": "duplicate_audit_id", "audit_id": audit_id},
        )

    except SQLAlchemyError as exc:
        elapsed = time.monotonic() - start
        write_latency.labels(event_type=event_type_val, layer=layer_val).observe(elapsed)
        logger.error(
            "audit_write_failed",
            extra={
                "extra_fields": {
                    "request_id": event.request_id,
                    "error": str(exc),
                    "layer": layer_val,
                }
            },
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error"},
        )

    else:
        elapsed = time.monotonic() - start
        latency_ms_recorded = round(elapsed * 1000)

        # Metrics — only on success (Req 8.2 / 8.3)
        writes_total.labels(event_type=event_type_val, layer=layer_val).inc()
        write_latency.labels(event_type=event_type_val, layer=layer_val).observe(elapsed)

        logger.info(
            "audit_event_written",
            extra={
                "extra_fields": {
                    "audit_id": audit_id,
                    "request_id": event.request_id,
                    "layer": layer_val,
                    "event_type": event_type_val,
                    "latency_ms": latency_ms_recorded,
                }
            },
        )

        return AuditEventResponse(
            audit_id=audit_id,
            request_id=event.request_id,
            timestamp_utc=timestamp_utc,
            user_id=event.user_id,
            department=event.department,
            layer=event.layer,
            event_type=event.event_type,
            model_used=event.model_used,
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            latency_ms=event.latency_ms,
            outcome=event.outcome,
            error_code=event.error_code,
            pii_actions=event.pii_actions,
            policy_decisions=event.policy_decisions,
        )


# ---------------------------------------------------------------------------
# POST /audit/events/batch
# ---------------------------------------------------------------------------


@router.post("/audit/events/batch", status_code=201)
async def batch_create_audit_events(batch: BatchWriteRequest, request: Request):
    """Insert a batch of audit events atomically and return inserted IDs (HTTP 201).

    All records are inserted inside a single transaction (engine.begin())
    so that a failure partway through rolls back the entire batch — no
    partial writes are ever visible to readers (Req 2.1 / 2.6).

    Satisfies: Req 2.1, 2.2, 2.6, 7.3, 7.4, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3
    """
    start = time.monotonic()

    # Resolve optional fields per record and collect resolved data (subtask 9.2)
    resolved: list[tuple[AuditEventCreate, str, str]] = []
    for event in batch.events:
        audit_id = event.audit_id or str(uuid.uuid4())
        timestamp_utc = event.timestamp_utc or (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )
        resolved.append((event, audit_id, timestamp_utc))

    engine = request.app.state.engine

    # Use the first event's labels as the batch-level label for the latency histogram.
    # Counter labels are per-record (incremented individually on success).
    first_event = batch.events[0]
    batch_layer = first_event.layer.value
    batch_event_type = first_event.event_type.value

    def _write() -> None:
        with engine.begin() as conn:
            conn.execute(
                audit_events.insert(),
                [_row_values(event, audit_id, timestamp_utc) for event, audit_id, timestamp_utc in resolved],
            )

    try:
        await run_db(get_db_executor(request.app), _write)

    except IntegrityError as exc:
        elapsed = time.monotonic() - start
        write_latency.labels(event_type=batch_event_type, layer=batch_layer).observe(elapsed)
        # Surface whichever audit_id triggered the conflict (if detectable).
        return JSONResponse(
            status_code=409,
            content={"error": "duplicate_audit_id", "detail": str(exc)},
        )

    except SQLAlchemyError as exc:
        elapsed = time.monotonic() - start
        write_latency.labels(event_type=batch_event_type, layer=batch_layer).observe(elapsed)
        logger.error(
            "audit_batch_write_failed",
            extra={
                "extra_fields": {
                    "request_id": first_event.request_id,
                    "error": str(exc),
                    "layer": batch_layer,
                }
            },
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error"},
        )

    else:
        elapsed = time.monotonic() - start
        latency_ms_recorded = round(elapsed * 1000)

        inserted_ids: list[str] = []
        for event, audit_id, _ in resolved:
            # Increment counter per successfully written record (Req 8.2)
            writes_total.labels(
                event_type=event.event_type.value, layer=event.layer.value
            ).inc()
            inserted_ids.append(audit_id)

        # Observe latency ONCE per batch (Req 8.3 / subtask 9.2)
        write_latency.labels(event_type=batch_event_type, layer=batch_layer).observe(elapsed)

        logger.info(
            "audit_batch_written",
            extra={
                "extra_fields": {
                    "audit_id": inserted_ids[0] if inserted_ids else None,
                    "request_id": first_event.request_id,
                    "layer": batch_layer,
                    "event_type": batch_event_type,
                    "latency_ms": latency_ms_recorded,
                    "inserted": len(inserted_ids),
                }
            },
        )

        return BatchWriteResponse(
            inserted=len(inserted_ids),
            audit_ids=inserted_ids,
        )
