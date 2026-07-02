"""
Audit event emitter for the API Gateway (Layer 1).

Writes structured audit events as JSON lines to stdout.
For POC, stdout logging is the designated audit channel — no write
to the Audit Store service is required at this layer.

Validates: Requirements 9.1–9.7
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from api_gateway.schemas.audit import AuditEvent


def build_audit_event(**kwargs) -> AuditEvent:
    """Create an AuditEvent with auto-filled infrastructure fields.

    Auto-fills:
        - ``audit_id``: fresh UUID v4
        - ``timestamp_utc``: current UTC time in ISO-8601 + "Z"
        - ``layer``: always ``"api_gateway"``

    Any caller-supplied value for ``layer`` is silently overridden to
    enforce the invariant that every event emitted from this module is
    tagged correctly.

    Args:
        **kwargs: Fields forwarded directly to the :class:`AuditEvent`
            constructor.  ``outcome`` is required and must be one of
            ``"pass"``, ``"block"``, or ``"error"``.

    Returns:
        A fully constructed :class:`AuditEvent`.
    """
    kwargs["audit_id"] = str(uuid.uuid4())
    kwargs["timestamp_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    kwargs["layer"] = "api_gateway"  # always enforced
    return AuditEvent(**kwargs)


def emit_audit_event(event: AuditEvent) -> None:
    """Serialize *event* as a single JSON line and write it to stdout.

    Ensures that:
    - ``audit_id`` is a non-empty UUID v4 (generates one if missing).
    - ``layer`` is always ``"api_gateway"``.
    - ``outcome`` must be one of ``"pass"``, ``"block"``, or ``"error"``
      (enforced by the :class:`AuditEvent` schema; callers receive a
      ``ValidationError`` if the value is invalid).

    Args:
        event: The :class:`AuditEvent` to emit.
    """
    # Ensure audit_id is always set
    if not event.audit_id:
        event = event.model_copy(update={"audit_id": str(uuid.uuid4())})

    # Enforce layer invariant
    if event.layer != "api_gateway":
        event = event.model_copy(update={"layer": "api_gateway"})

    print(event.model_dump_json())
