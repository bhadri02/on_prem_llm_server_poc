# Feature: agent-framework, Property 9: Every audit event has mandatory invariant fields
"""
tests/test_property_audit_invariants.py

Property-based and deterministic unit tests for the audit event invariant fields.

Property 9: Every audit event has mandatory invariant fields
Validates: Requirements 11.4, 11.5

Coverage:
  - Hypothesis property: every audit event emitted contains a valid UUID v4 audit_id,
    layer == "agent", and outcome in the permitted set
  - Hypothesis property: detail fields with embedded newlines/special chars produce
    exactly one newline in the output (the trailing one from print)
  - Hypothesis property: emit_audit_event() never raises, regardless of input
  - Deterministic unit tests: specific well-formed events verify all invariant fields
"""

import contextlib
import io
import json
import re
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent_framework.audit import emit_audit_event

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_VALID_OUTCOMES = {"pass", "block", "error", "max_steps_reached"}
_VALID_EVENT_TYPES = ["agent_session_start", "agent_tool_call", "agent_session_complete"]


# ---------------------------------------------------------------------------
# Property 9 — test 1: invariant fields present on every valid event
# ---------------------------------------------------------------------------


@given(
    event_type=st.sampled_from(_VALID_EVENT_TYPES),
    outcome=st.sampled_from(["pass", "block", "error", "max_steps_reached"]),
    extra_fields=st.dictionaries(
        keys=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters="_",
            ),
        ),
        values=st.one_of(st.text(max_size=50), st.integers(), st.booleans()),
        max_size=5,
    ),
)
@settings(max_examples=100)
def test_audit_event_invariant_fields(
    event_type: str, outcome: str, extra_fields: dict
):
    """
    **Validates: Requirements 11.4, 11.5**

    Property 9: For every combination of valid event_type, outcome, and
    arbitrary extra fields, the JSON line emitted by emit_audit_event() must:
      - contain a valid UUID v4 in audit_id
      - have layer == "agent"
      - have outcome in the permitted set
      - produce a raw string with no embedded newlines
    """
    audit_id = str(uuid.uuid4())
    event = {
        "audit_id": audit_id,
        "layer": "agent",
        "outcome": outcome,
        "event_type": event_type,
        **extra_fields,
    }

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        emit_audit_event(event)

    raw = buf.getvalue()

    # Exactly one line was written; strip the trailing newline to get the JSON
    line = raw.rstrip("\n")
    assert "\n" not in line, (
        f"Embedded newline found in emitted JSON line for event_type={event_type!r}. "
        f"Raw output: {raw!r}"
    )
    assert line == raw.strip(), (
        f"Stripping whitespace changed the line — unexpected leading/trailing "
        f"whitespace. Raw output: {raw!r}"
    )

    parsed = json.loads(line)

    # audit_id must be the UUID v4 we supplied
    assert "audit_id" in parsed, "audit_id field missing from parsed event"
    assert _UUID4_RE.match(parsed["audit_id"]), (
        f"audit_id {parsed['audit_id']!r} does not match UUID v4 pattern"
    )
    assert parsed["audit_id"] == audit_id, (
        f"audit_id was mutated: expected {audit_id!r}, got {parsed['audit_id']!r}"
    )

    # layer must be "agent"
    assert parsed.get("layer") == "agent", (
        f"Expected layer='agent', got {parsed.get('layer')!r}"
    )

    # outcome must be in the permitted set
    assert parsed.get("outcome") in _VALID_OUTCOMES, (
        f"outcome {parsed.get('outcome')!r} is not in permitted set {_VALID_OUTCOMES}"
    )


# ---------------------------------------------------------------------------
# Property 9 — test 2: no embedded newlines even with special-character details
# ---------------------------------------------------------------------------


@given(detail=st.text(min_size=0, max_size=200))
@settings(max_examples=100)
def test_audit_event_no_embedded_newlines(detail: str):
    """
    **Validates: Requirements 11.4, 11.5**

    Property 9 (corollary): Even when a field value contains newline characters,
    carriage returns, or other control characters, the emitted output must contain
    exactly one newline — the trailing one appended by print().
    """
    event = {
        "audit_id": str(uuid.uuid4()),
        "layer": "agent",
        "outcome": "pass",
        "event_type": "agent_session_start",
        "detail": detail,
    }

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        emit_audit_event(event)

    output = buf.getvalue()

    assert output.count("\n") == 1, (
        f"Expected exactly 1 newline in output, found {output.count(chr(10))}. "
        f"detail={detail!r}, output={output!r}"
    )


# ---------------------------------------------------------------------------
# Property 9 — test 3: emit_audit_event() never raises
# ---------------------------------------------------------------------------


@given(
    arbitrary_dict=st.dictionaries(
        keys=st.text(max_size=20),
        values=st.one_of(
            st.text(max_size=50),
            st.integers(),
            st.none(),
            st.booleans(),
        ),
        max_size=10,
    )
)
@settings(max_examples=100)
def test_emit_audit_event_never_raises(arbitrary_dict: dict):
    """
    **Validates: Requirements 11.4, 11.5**

    Property 9 (safety): emit_audit_event() must never raise an exception,
    regardless of what is passed to it. Errors are silently swallowed so
    that a broken audit write never aborts a user request.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            emit_audit_event(arbitrary_dict)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"emit_audit_event() raised {type(exc).__name__}: {exc!r} "
            f"for input {arbitrary_dict!r}"
        )


# ---------------------------------------------------------------------------
# Deterministic unit tests
# ---------------------------------------------------------------------------


class TestAuditEventInvariantsUnit:
    """
    Deterministic tests verifying that specific well-formed audit events
    produce correct JSON output with all invariant fields intact.
    """

    def _capture_event(self, event: dict) -> dict:
        """Emit *event*, capture stdout, return parsed JSON."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            emit_audit_event(event)
        raw = buf.getvalue().rstrip("\n")
        return json.loads(raw)

    def test_session_start_event(self):
        """agent_session_start: all invariant fields must be present and valid."""
        audit_id = str(uuid.uuid4())
        event = {
            "audit_id": audit_id,
            "layer": "agent",
            "outcome": "pass",
            "event_type": "agent_session_start",
            "session_id": "sess-001",
            "user_id": "user-abc",
        }
        parsed = self._capture_event(event)

        assert _UUID4_RE.match(parsed["audit_id"]), (
            f"audit_id {parsed['audit_id']!r} is not a valid UUID v4"
        )
        assert parsed["layer"] == "agent"
        assert parsed["outcome"] in _VALID_OUTCOMES
        assert parsed["event_type"] == "agent_session_start"

    def test_tool_call_event(self):
        """agent_tool_call: invariants hold and tool_name is preserved."""
        audit_id = str(uuid.uuid4())
        event = {
            "audit_id": audit_id,
            "layer": "agent",
            "outcome": "pass",
            "event_type": "agent_tool_call",
            "tool_name": "calculator",
        }
        parsed = self._capture_event(event)

        assert _UUID4_RE.match(parsed["audit_id"]), (
            f"audit_id {parsed['audit_id']!r} is not a valid UUID v4"
        )
        assert parsed["layer"] == "agent"
        assert parsed["outcome"] in _VALID_OUTCOMES
        assert parsed["event_type"] == "agent_tool_call"
        assert parsed.get("tool_name") == "calculator", (
            f"tool_name missing or incorrect: {parsed.get('tool_name')!r}"
        )

    def test_session_complete_event(self):
        """agent_session_complete: invariants hold and outcome == 'max_steps_reached'."""
        audit_id = str(uuid.uuid4())
        event = {
            "audit_id": audit_id,
            "layer": "agent",
            "outcome": "max_steps_reached",
            "event_type": "agent_session_complete",
            "steps_taken": 10,
        }
        parsed = self._capture_event(event)

        assert _UUID4_RE.match(parsed["audit_id"]), (
            f"audit_id {parsed['audit_id']!r} is not a valid UUID v4"
        )
        assert parsed["layer"] == "agent"
        assert parsed["outcome"] == "max_steps_reached", (
            f"Expected outcome='max_steps_reached', got {parsed['outcome']!r}"
        )
        assert parsed["event_type"] == "agent_session_complete"
