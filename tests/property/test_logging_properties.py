"""
Property-based tests for the Audit Store logging behaviour.

Properties covered:
  - Property 17: Every log entry emitted by audit_store is single-line JSON
                 with mandatory timestamp (ISO-8601 UTC), level, and no
                 embedded newlines.

**Validates: Requirements 9.1, 9.4**
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import asyncio
import datetime
import io
import json
import logging
import os
import re
from uuid import uuid4

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import httpx
from hypothesis import given, settings, strategies as st

# ---------------------------------------------------------------------------
# Register the 'ci' Hypothesis settings profile.
# Must be done before any @given-decorated functions are defined.
# deadline=None: see test_write_properties.py's identical note (real DB
# round trips per example are I/O-bound and can occasionally exceed the
# default 200ms wall-clock deadline under load).
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, deadline=None)
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from audit_store.models import LayerEnum, EventTypeEnum, OutcomeEnum, UUID4_RE
from tests.audit_store_test_utils import make_audit_store_app

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------
AUDIT_API_KEY = "test-key"

# ISO-8601 timestamp regex: YYYY-MM-DDTHH:MM:SS (with optional fractional
# seconds and optional Z / timezone offset suffix)
_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


# ---------------------------------------------------------------------------
# Shared helper: build a fresh in-memory app for one Hypothesis example
# ---------------------------------------------------------------------------

def _make_app():
    """Build a fresh in-memory FastAPI app for one Hypothesis example.

    Returns (application, engine) — see tests/audit_store_test_utils.py.
    """
    return make_audit_store_app()


# ---------------------------------------------------------------------------
# Helper: redirect all audit_store logger handler streams to a buffer
# ---------------------------------------------------------------------------

def _redirect_audit_loggers(buf: io.StringIO):
    """
    Return a list of (logger, handler, original_stream) tuples after
    re-pointing every StreamHandler on every audit_store.* logger to *buf*.

    Only redirects handlers that use our JSONFormatter so that pytest's own
    caplog plain-text handler is not included.

    Call _restore_audit_loggers(saved) in a finally block.
    """
    from audit_store.logging_config import JSONFormatter  # local import to avoid cycles

    saved = []
    for name, logger_or_ref in logging.Logger.manager.loggerDict.items():
        if not name.startswith("audit_store"):
            continue
        # loggerDict values may be PlaceHolder objects (not Logger instances)
        if not isinstance(logger_or_ref, logging.Logger):
            continue
        for handler in logger_or_ref.handlers:
            if (
                isinstance(handler, logging.StreamHandler)
                and isinstance(handler.formatter, JSONFormatter)
            ):
                saved.append((logger_or_ref, handler, handler.stream))
                handler.stream = buf
    return saved


def _restore_audit_loggers(saved):
    """Restore original streams from the list returned by _redirect_audit_loggers."""
    for _lgr, handler, original_stream in saved:
        handler.stream = original_stream


# ---------------------------------------------------------------------------
# Property 17 — every log entry is single-line JSON
# ---------------------------------------------------------------------------


@given(operation=st.sampled_from(["write_valid", "write_invalid", "query"]))
@settings(max_examples=100)
def test_every_log_entry_is_single_line_json(operation):
    """**Validates: Requirements 9.1, 9.4**

    For any operation performed against the app (valid write, invalid write,
    or query), every log line emitted by audit_store loggers SHALL:

    1. Be valid JSON (json.loads succeeds).
    2. Contain a ``timestamp`` field parseable as ISO-8601 UTC.
    3. Contain a ``level`` field whose value is one of DEBUG / INFO /
       WARNING / ERROR / CRITICAL.
    4. Contain no embedded newline characters (``\\n``).
    """

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        buf = io.StringIO()
        saved = _redirect_audit_loggers(buf)

        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": AUDIT_API_KEY},
            ) as client:
                if operation == "write_valid":
                    payload = {
                        "request_id": str(uuid4()),
                        "layer": LayerEnum.inference.value,
                        "event_type": EventTypeEnum.inference_start.value,
                        "outcome": OutcomeEnum.pass_.value,
                    }
                    response = await client.post("/audit/events", json=payload)
                    assert response.status_code == 201, (
                        f"write_valid: expected 201, got {response.status_code}. "
                        f"Body: {response.text}"
                    )

                elif operation == "write_invalid":
                    payload = {
                        "request_id": "not-a-uuid",
                        "layer": LayerEnum.inference.value,
                        "event_type": EventTypeEnum.inference_start.value,
                        "outcome": OutcomeEnum.pass_.value,
                    }
                    response = await client.post("/audit/events", json=payload)
                    assert response.status_code == 422, (
                        f"write_invalid: expected 422, got {response.status_code}. "
                        f"Body: {response.text}"
                    )

                else:  # query
                    response = await client.get("/audit/events")
                    assert response.status_code == 200, (
                        f"query: expected 200, got {response.status_code}. "
                        f"Body: {response.text}"
                    )
        finally:
            _restore_audit_loggers(saved)
            conn.dispose()

        return buf.getvalue()

    captured = asyncio.run(_run())

    # Split on newlines; filter out blank lines (e.g. trailing newline)
    lines = [line for line in captured.split("\n") if line.strip()]

    # If no log lines were emitted the property trivially passes — some
    # operations (e.g. write_invalid rejected at Pydantic validation) may
    # not reach application-level logging code.
    for line in lines:
        # --- Assertion 4: no embedded newline in the raw line string ---
        # (guaranteed by the split, but also validates the formatter)
        assert "\n" not in line, (
            f"Log line contains an embedded newline: {line!r}"
        )

        # --- Assertion 1: valid JSON ---
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"Log line is not valid JSON ({exc}): {line!r}"
            ) from exc

        # --- Assertion 2: timestamp present and ISO-8601 parseable ---
        assert "timestamp" in data, (
            f"Log line missing 'timestamp' field: {line!r}"
        )
        ts = data["timestamp"]
        # Strip trailing "Z" (not valid in Python's fromisoformat before 3.11)
        ts_stripped = ts.rstrip("Z")
        try:
            datetime.datetime.fromisoformat(ts_stripped)
        except ValueError:
            # Fall back to regex check for robustness
            assert _ISO8601_RE.match(ts), (
                f"timestamp {ts!r} does not look like ISO-8601: {line!r}"
            )

        # --- Assertion 3: level is a known log level ---
        assert "level" in data, (
            f"Log line missing 'level' field: {line!r}"
        )
        assert data["level"] in _VALID_LEVELS, (
            f"'level' value {data['level']!r} is not one of {_VALID_LEVELS}: "
            f"{line!r}"
        )
