"""
agent_framework/audit.py

Audit event emitter for the Agent Framework (Layer 6).

Writes structured JSON audit events to stdout as single-line JSON records.
This module never raises — any serialisation failure is silently swallowed
so that a broken audit write never aborts a user request.

Usage::

    from agent_framework.audit import emit_audit_event

    emit_audit_event({
        "audit_id": str(uuid.uuid4()),
        "request_id": request_id,
        "session_id": session_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "layer": "agent",
        "event_type": "agent_session_start",
        "outcome": "pass",
    })

Requirements: 11.1–11.6
# TODO: implement in task 7
"""

import json
import sys
from typing import Any


def emit_audit_event(event: dict[str, Any]) -> None:
    """Write *event* as a single JSON line to stdout.

    - Uses ``json.dumps(..., default=str)`` so non-serialisable values
      (datetimes, UUIDs, etc.) are coerced to strings without raising.
    - ``print(..., flush=True)`` ensures the line is flushed immediately,
      which is required for log aggregators reading stdout in streaming mode.
    - Never raises; any exception is swallowed to protect the caller.

    Args:
        event: A dictionary containing the audit event fields.
    """
    try:
        line = json.dumps(event, default=str, ensure_ascii=False)
        print(line, flush=True, file=sys.stdout)
    except Exception:  # noqa: BLE001
        # Audit emission must never abort a request.
        pass
