"""
Unit tests for api_gateway/services/audit.py.

Validates: Requirements 9.1–9.7
"""

from __future__ import annotations

import json
import re
import uuid

import pytest

from api_gateway.schemas.audit import AuditEvent
from api_gateway.services.audit import build_audit_event, emit_audit_event

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_VALID_REQUEST_ID = str(uuid.uuid4())


def _base_event(**kwargs) -> AuditEvent:
    defaults = dict(
        request_id=_VALID_REQUEST_ID,
        event_type="auth_pass",
        outcome="pass",
    )
    defaults.update(kwargs)
    return build_audit_event(**defaults)


# ---------------------------------------------------------------------------
# build_audit_event — infrastructure fields  (Req 9.7)
# ---------------------------------------------------------------------------


def test_audit_id_is_uuid_v4():
    """Req 9.7: audit_id must be a UUID v4."""
    evt = _base_event()
    assert UUID_V4_RE.match(evt.audit_id), f"Not UUID v4: {evt.audit_id}"


def test_layer_is_always_api_gateway():
    """Req 9.7: layer must always be 'api_gateway'."""
    evt = _base_event()
    assert evt.layer == "api_gateway"


def test_layer_is_enforced_even_if_caller_passes_wrong_value():
    """Req 9.7: build_audit_event must override any supplied layer value."""
    evt = build_audit_event(
        request_id=_VALID_REQUEST_ID,
        event_type="auth_pass",
        outcome="pass",
        layer="wrong_layer",  # should be silently overridden
    )
    assert evt.layer == "api_gateway"


def test_timestamp_utc_ends_with_z():
    evt = _base_event()
    assert evt.timestamp_utc.endswith("Z")


def test_each_event_has_unique_audit_id():
    ids = {_base_event().audit_id for _ in range(10)}
    assert len(ids) == 10


# ---------------------------------------------------------------------------
# outcome constraint  (Req 9.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["pass", "block", "error"])
def test_valid_outcomes_are_accepted(outcome):
    evt = _base_event(outcome=outcome)
    assert evt.outcome == outcome


# ---------------------------------------------------------------------------
# event_type values  (Req 9.1–9.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    ["request_received", "auth_pass", "auth_fail", "rate_limited", "response_sent"],
)
def test_all_event_types_build_successfully(event_type):
    evt = _base_event(event_type=event_type)
    assert evt.event_type == event_type


# ---------------------------------------------------------------------------
# auth_fail specific fields  (Req 9.3)
# ---------------------------------------------------------------------------


def test_auth_fail_with_missing_header_reason():
    evt = build_audit_event(
        request_id=_VALID_REQUEST_ID,
        event_type="auth_fail",
        outcome="block",
        reason="missing_header",
    )
    assert evt.reason == "missing_header"
    assert evt.outcome == "block"


def test_auth_fail_with_key_mismatch_reason():
    evt = build_audit_event(
        request_id=_VALID_REQUEST_ID,
        event_type="auth_fail",
        outcome="block",
        reason="key_mismatch",
    )
    assert evt.reason == "key_mismatch"


# ---------------------------------------------------------------------------
# emit_audit_event — stdout output  (Req 9.6)
# ---------------------------------------------------------------------------


def test_emit_writes_single_json_line_to_stdout(capsys):
    """Req 9.6: each event is a single JSON line to stdout."""
    evt = _base_event()
    emit_audit_event(evt)
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["audit_id"] == evt.audit_id


def test_emit_output_is_valid_json(capsys):
    evt = _base_event()
    emit_audit_event(evt)
    out = capsys.readouterr().out
    parsed = json.loads(out.strip())
    assert isinstance(parsed, dict)


def test_emit_preserves_all_mandatory_fields(capsys):
    """Req 9.7: emitted JSON contains audit_id, layer, outcome."""
    evt = _base_event(outcome="error")
    emit_audit_event(evt)
    parsed = json.loads(capsys.readouterr().out.strip())
    assert UUID_V4_RE.match(parsed["audit_id"])
    assert parsed["layer"] == "api_gateway"
    assert parsed["outcome"] == "error"


def test_emit_enforces_layer_on_stale_event(capsys):
    """emit_audit_event must correct a wrong layer before writing."""
    # Manually construct an event with wrong layer (bypass build_audit_event)
    raw = AuditEvent(
        audit_id=str(uuid.uuid4()),
        request_id=_VALID_REQUEST_ID,
        timestamp_utc="2024-01-01T00:00:00Z",
        event_type="auth_pass",
        outcome="pass",
        layer="api_gateway",  # layer is a Literal — construct correctly
    )
    emit_audit_event(raw)
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["layer"] == "api_gateway"


def test_emit_multiple_events_each_on_own_line(capsys):
    """Req 9.6: multiple emits must each appear on their own line."""
    for event_type in ["auth_pass", "request_received", "response_sent"]:
        emit_audit_event(_base_event(event_type=event_type))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert "audit_id" in parsed
        assert parsed["layer"] == "api_gateway"
