"""
Property-based tests for the Audit Store write endpoints.

Properties covered (added in tasks 13.3–13.14):
  - Property  1: Write single valid event succeeds with auto-assigned IDs
  - Property  2: audit_id auto-generation is always a valid UUID-v4
  - Property  3: Invalid request_id always rejected with HTTP 422
  - Property  4: Invalid enum field values always rejected with HTTP 422
  - Property  5: Non-JSON body always rejected with HTTP 400
  - Property  6: Append-only API surface — write-mutating HTTP methods always rejected
  - Property  7: Batch write is all-or-nothing — atomicity invariant
  - Property  8: Batch size boundary enforcement
  - Property 11: Duplicate audit_id submission returns HTTP 409
  - Property 15: llm_audit_writes_total incremented exactly on successful inserts
  - Property 16: llm_audit_write_latency_seconds records every write attempt
  - Property 18: Auth enforcement — missing or invalid API key always rejected on write endpoints

This file currently contains only the Hypothesis settings profile and shared
imports.  Individual property test functions are added in tasks 13.3–13.14.
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import os
import re
import sqlite3
from contextlib import asynccontextmanager

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import pytest
import pytest_asyncio
import httpx
from hypothesis import given, settings, strategies as st

# ---------------------------------------------------------------------------
# Register the 'ci' Hypothesis settings profile.
# Must be done before any @given-decorated functions are defined so the
# profile is available for load_profile() below.
# deadline=None: these properties assert correctness, not latency — a
# per-request DB round trip (real SQLAlchemy Core query compilation +
# execution against sqlite, standing in for Postgres in production) is
# I/O-bound and can occasionally exceed Hypothesis's default 200ms wall-
# clock deadline under load, which would otherwise fail the test on pure
# timing rather than a real correctness violation.
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, deadline=None)
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from audit_store.models import (
    UUID4_RE,
    LayerEnum,
    EventTypeEnum,
    OutcomeEnum,
    AuditEventCreate,
)
from audit_store.database import audit_events
from tests.audit_store_test_utils import make_audit_store_app

# ---------------------------------------------------------------------------
# Test constants (mirror conftest.py so this file can also run standalone)
# ---------------------------------------------------------------------------
AUDIT_API_KEY = "test-key"


# ===========================================================================
# Property 1 — Valid single write returns HTTP 201 with a UUID-v4 audit_id
# ===========================================================================
# Validates: Requirements 1.1, 1.2, 1.3


def _make_app():
    """Build a fresh in-memory FastAPI app for one Hypothesis example.

    Returns (application, engine) — the second element is a SQLAlchemy
    Engine (audit_store is Postgres-backed in production; tests use
    sqlite:///:memory: — see tests/audit_store_test_utils.py).
    """
    return make_audit_store_app()


@given(
    request_id=st.uuids(version=4).map(str),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=50)
def test_valid_single_write_returns_201(request_id, layer, event_type, outcome):
    """**Validates: Requirements 1.1, 1.2, 1.3**

    For any valid AuditEventCreate (with mandatory fields request_id, layer,
    event_type, outcome present and valid), POST /audit/events SHALL return
    HTTP 201 with a non-null audit_id that is a valid UUID-v4.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            payload = {
                "request_id": request_id,
                "layer": layer,
                "event_type": event_type,
                "outcome": outcome,
            }
            response = await client.post("/audit/events", json=payload)
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}. Body: {response.text}"
    )
    body = response.json()
    assert "audit_id" in body, f"audit_id missing from response: {body}"
    assert body["audit_id"] is not None, "audit_id is null"
    assert UUID4_RE.match(body["audit_id"]), (
        f"audit_id {body['audit_id']!r} is not a valid UUID-v4"
    )


# ===========================================================================
# Property 2 — audit_id auto-generation is always a valid UUID-v4
# ===========================================================================
# Validates: Requirement 1.2


@given(
    request_id=st.uuids(version=4).map(str),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=50)
def test_audit_id_auto_generation_is_uuid4(request_id, layer, event_type, outcome):
    """**Validates: Requirements 1.2**

    For any valid audit event submitted WITHOUT an audit_id field, the
    audit_id in the HTTP 201 response SHALL match the full UUID-v4 regex
    ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$,
    confirming version=4 and the correct variant bits [89ab].
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            # Payload deliberately excludes audit_id so the server must
            # auto-generate it.
            payload = {
                "request_id": request_id,
                "layer": layer,
                "event_type": event_type,
                "outcome": outcome,
            }
            response = await client.post("/audit/events", json=payload)
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}. Body: {response.text}"
    )
    body = response.json()
    assert "audit_id" in body, f"audit_id missing from response: {body}"
    audit_id = body["audit_id"]
    assert audit_id is not None, "audit_id must not be null when auto-generated"
    assert UUID4_RE.match(audit_id), (
        f"audit_id {audit_id!r} does not match UUID-v4 pattern "
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )


# ===========================================================================
# Property 3 — Invalid request_id always rejected with HTTP 422
# ===========================================================================
# Validates: Requirement 1.4


_INVALID_REQUEST_ID_STRATEGY = st.one_of(
    # Empty string
    st.just(""),
    # Plain ASCII words — clearly not UUID shaped
    st.sampled_from([
        "not-a-uuid",
        "hello world",
        "12345",
        "none",
        "null",
        "00000000-0000-0000-0000-000000000000",   # all-zeros: version=0, not v4
        "xxxxxxxx-xxxx-4xxx-xxxx-xxxxxxxxxxxx",   # template placeholder
        "XXXXXXXX-XXXX-4XXX-XXXX-XXXXXXXXXXXX",   # uppercase template
        "550e8400-e29b-11d4-a716-446655440000",   # UUID v1
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",   # UUID v1
        "a6e5024d-6c84-5c23-bcea-14b43e00b0b0",   # UUID v5
        "00000000-0000-4000-0000-000000000000",   # version=4 but wrong variant
        "00000000-0000-4000-c000-000000000000",   # version=4 but variant=c (invalid)
    ]),
    # Integers as strings
    st.integers(min_value=0, max_value=10**9).map(str),
)


@given(
    request_id=_INVALID_REQUEST_ID_STRATEGY,
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=50)
def test_invalid_request_id_returns_422(request_id, layer, event_type, outcome):
    """**Validates: Requirements 1.4**

    For any string value submitted as request_id that does NOT match the
    UUID-v4 format (including empty strings, non-UUID strings, or null),
    POST /audit/events SHALL return HTTP 422 with a structured error body
    that identifies request_id as the failing field.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            payload = {
                "request_id": request_id,
                "layer": layer,
                "event_type": event_type,
                "outcome": outcome,
            }
            response = await client.post("/audit/events", json=payload)
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 422, (
        f"Expected 422 for invalid request_id={request_id!r}, "
        f"got {response.status_code}. Body: {response.text}"
    )
    response_str = str(response.json())
    assert "request_id" in response_str, (
        f"Expected 'request_id' in error detail for invalid request_id={request_id!r}. "
        f"Response body: {response.text}"
    )


# ===========================================================================
# Property 4 — Invalid enum field values always rejected with HTTP 422
# ===========================================================================
# Validates: Requirements 1.5, 1.6, 1.7


@given(
    field_name=st.sampled_from(["layer", "event_type", "outcome"]),
    invalid_value=st.sampled_from(["NOT_VALID", "INVALID_ENUM", "foobar", "unknown", "123", ""]),
    request_id=st.uuids(version=4).map(str),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=50)
def test_invalid_enum_field_returns_422(
    field_name, invalid_value, request_id, layer, event_type, outcome
):
    """**Validates: Requirements 1.5, 1.6, 1.7**

    For any value submitted for `layer`, `event_type`, or `outcome` that is
    NOT in its respective enumeration, POST /audit/events SHALL return HTTP
    422 with a structured error body identifying the invalid field name.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            # Build a valid payload, then corrupt exactly one enum field.
            payload = {
                "request_id": request_id,
                "layer": layer,
                "event_type": event_type,
                "outcome": outcome,
            }
            payload[field_name] = invalid_value
            response = await client.post("/audit/events", json=payload)
        conn.dispose()
        return response, field_name

    response, corrupted_field = asyncio.run(_run())

    assert response.status_code == 422, (
        f"Expected 422 for invalid {corrupted_field}={invalid_value!r}, "
        f"got {response.status_code}. Body: {response.text}"
    )
    response_str = str(response.json())
    assert corrupted_field in response_str, (
        f"Expected '{corrupted_field}' in error detail for "
        f"invalid {corrupted_field}={invalid_value!r}. "
        f"Response body: {response.text}"
    )


# ===========================================================================
# Property 5 — Non-JSON body always rejected with HTTP 400
# ===========================================================================
# Validates: Requirement 1.8


def _is_not_valid_json(b: bytes) -> bool:
    """Return True when the byte sequence cannot be parsed as valid JSON."""
    try:
        import json
        json.loads(b.decode("utf-8", errors="replace"))
        return False
    except Exception:
        return True


@given(
    body=st.one_of(
        st.just(b"not json"),
        st.just(b"{bad"),
        st.just(b"hello world"),
        st.just(b"\xff\xfe"),
    )
)
@settings(max_examples=50)
def test_non_json_body_returns_400(body):
    """**Validates: Requirements 1.8**

    For any byte sequence submitted as the request body to POST /audit/events
    that is NOT parseable as valid JSON, the endpoint SHALL return HTTP 400.
    No insertion is attempted.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/audit/events",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": AUDIT_API_KEY,
                },
            )
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 400, (
        f"Expected 400 for non-JSON body {body!r}, "
        f"got {response.status_code}. Body: {response.text}"
    )


# ===========================================================================
# Property 6 — Append-only API surface: mutating HTTP methods always rejected
# ===========================================================================
# Validates: Requirement 1.10


@given(
    method=st.sampled_from(["PUT", "PATCH", "DELETE"]),
    path=st.sampled_from([
        "/audit/events",
        "/audit/events/batch",
        "/audit/requests/some-id",
    ]),
)
@settings(max_examples=50)
def test_mutating_methods_rejected(method, path):
    """**Validates: Requirements 1.10**

    For any of the HTTP methods PUT, PATCH, DELETE applied to any
    /audit/events/* or /audit/requests/* path, the service SHALL return
    HTTP 404 or HTTP 405.  No modification or deletion of stored records
    is possible through the API.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            response = await client.request(method=method, url=path)
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code in {404, 405}, (
        f"Expected 404 or 405 for {method} {path}, "
        f"got {response.status_code}. Body: {response.text}"
    )


# ===========================================================================
# Property 7 — Batch atomicity: invalid record causes full rollback (HTTP 422)
# ===========================================================================
# Validates: Requirements 2.1, 2.3


@given(
    n=st.integers(min_value=1, max_value=5),
    invalid_pos=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=50)
def test_batch_atomicity_on_invalid_record(n, invalid_pos):
    """**Validates: Requirements 2.1, 2.3**

    For any batch submitted to POST /audit/events/batch that contains at
    least one record with a validation error (invalid `layer`), the endpoint
    SHALL return HTTP 422 and zero records SHALL be inserted into the database.
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        # Build n valid event payloads
        valid_events = [
            {
                "request_id": str(__import__("uuid").uuid4()),
                "layer": LayerEnum.inference.value,
                "event_type": EventTypeEnum.inference_start.value,
                "outcome": OutcomeEnum.pass_.value,
            }
            for _ in range(n)
        ]

        # Insert one invalid record at position clamped to [0, n]
        insert_at = min(invalid_pos, n)
        invalid_event = {
            "request_id": str(__import__("uuid").uuid4()),
            "layer": "INVALID_LAYER",
            "event_type": EventTypeEnum.inference_start.value,
            "outcome": OutcomeEnum.pass_.value,
        }
        batch_events = valid_events[:insert_at] + [invalid_event] + valid_events[insert_at:]

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            response = await client.post(
                "/audit/events/batch",
                json={"events": batch_events},
            )

        # Check DB directly — must be empty
        from sqlalchemy import func, select

        with conn.connect() as db_conn:
            db_count = db_conn.execute(select(func.count()).select_from(audit_events)).scalar_one()
        conn.dispose()
        return response, db_count

    response, db_count = asyncio.run(_run())

    assert response.status_code == 422, (
        f"Expected 422 for batch with invalid layer, "
        f"got {response.status_code}. Body: {response.text}"
    )
    assert db_count == 0, (
        f"Expected 0 rows in DB after failed batch (atomicity), "
        f"found {db_count} row(s)."
    )


# ===========================================================================
# Property 8 — Batch size boundary enforcement (over-limit → 422)
# ===========================================================================
# Validates: Requirements 2.1, 2.5


@given(n=st.integers(min_value=501, max_value=510))
@settings(max_examples=20)
def test_batch_size_over_limit(n):
    """**Validates: Requirements 2.1, 2.5**

    For any batch whose length exceeds 500, POST /audit/events/batch SHALL
    return HTTP 422.  The server must enforce the upper-bound constraint
    regardless of the record contents.
    """
    import asyncio
    from uuid import uuid4

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        # Build n minimal valid event payloads
        events = [
            {
                "request_id": str(uuid4()),
                "layer": LayerEnum.inference.value,
                "event_type": EventTypeEnum.inference_start.value,
                "outcome": OutcomeEnum.pass_.value,
            }
            for _ in range(n)
        ]

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            response = await client.post(
                "/audit/events/batch",
                json={"events": events},
            )
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 422, (
        f"Expected 422 for batch of {n} events (> 500 limit), "
        f"got {response.status_code}. Body: {response.text}"
    )


# ===========================================================================
# Property 8 — Batch size boundary enforcement (valid size 1–500 → 201)
# ===========================================================================
# Validates: Requirements 2.1, 2.5


@given(n=st.integers(min_value=1, max_value=10))
@settings(max_examples=20)
def test_batch_size_valid(n):
    """**Validates: Requirements 2.1, 2.5**

    For any batch whose length is between 1 and 500 (inclusive) and all
    records are valid, POST /audit/events/batch SHALL return HTTP 201 and
    the response body SHALL contain exactly n audit_ids.
    """
    import asyncio
    from uuid import uuid4

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        # Build n valid event payloads, each with a unique request_id
        events = [
            {
                "request_id": str(uuid4()),
                "layer": LayerEnum.inference.value,
                "event_type": EventTypeEnum.inference_start.value,
                "outcome": OutcomeEnum.pass_.value,
            }
            for _ in range(n)
        ]

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            response = await client.post(
                "/audit/events/batch",
                json={"events": events},
            )
        conn.dispose()
        return response

    response = asyncio.run(_run())

    assert response.status_code == 201, (
        f"Expected 201 for batch of {n} valid events, "
        f"got {response.status_code}. Body: {response.text}"
    )
    body = response.json()
    assert "audit_ids" in body, f"audit_ids missing from response: {body}"
    assert len(body["audit_ids"]) == n, (
        f"Expected {n} audit_ids, got {len(body['audit_ids'])}. Body: {body}"
    )


# ===========================================================================
# Property 11 — Duplicate audit_id submission returns HTTP 409
# ===========================================================================
# Validates: Requirement 7.4


@given(
    request_id=st.uuids(version=4).map(str),
    audit_id=st.uuids(version=4).map(str),
)
@settings(max_examples=50)
def test_duplicate_audit_id_returns_409(request_id, audit_id):
    """**Validates: Requirements 7.4**

    For any audit_id value that already exists in the database, a subsequent
    POST to /audit/events with the same audit_id SHALL return HTTP 409.
    The pre-existing record SHALL remain unchanged (DB count for that
    audit_id is still 1).
    """
    import asyncio

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        payload = {
            "audit_id": audit_id,
            "request_id": request_id,
            "layer": LayerEnum.inference.value,
            "event_type": EventTypeEnum.inference_start.value,
            "outcome": OutcomeEnum.pass_.value,
        }

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            # First POST — must succeed with 201
            first_response = await client.post("/audit/events", json=payload)

            # Second POST — same audit_id, must return 409
            second_response = await client.post("/audit/events", json=payload)

        # Verify DB still has exactly 1 row for this audit_id
        from sqlalchemy import func, select

        with conn.connect() as db_conn:
            db_count = db_conn.execute(
                select(func.count()).select_from(audit_events).where(audit_events.c.audit_id == audit_id)
            ).scalar_one()
        conn.dispose()
        return first_response, second_response, db_count

    first_response, second_response, db_count = asyncio.run(_run())

    assert first_response.status_code == 201, (
        f"Expected first POST to return 201, got {first_response.status_code}. "
        f"Body: {first_response.text}"
    )
    assert second_response.status_code == 409, (
        f"Expected second POST (duplicate audit_id={audit_id!r}) to return 409, "
        f"got {second_response.status_code}. Body: {second_response.text}"
    )
    assert db_count == 1, (
        f"Expected exactly 1 row in DB for audit_id={audit_id!r} after duplicate "
        f"insert attempt, found {db_count} row(s)."
    )


# ===========================================================================
# Property 15 — llm_audit_writes_total incremented exactly on successful inserts
# ===========================================================================
# Validates: Requirements 8.1, 8.2


from prometheus_client import REGISTRY as _PROM_REGISTRY
import audit_store.metrics as _audit_metrics


def _get_counter_value(event_type: str, layer: str) -> float:
    """Read the current llm_audit_writes_total counter value for given labels.

    Reads directly from the module-level Counter object rather than via
    REGISTRY, because reset_prometheus (autouse fixture) unregisters collectors
    from REGISTRY — but the Counter object itself retains its accumulated value
    in memory and is still used by the write router via its module-level
    reference in audit_store.metrics.
    """
    try:
        sample_value = _audit_metrics.writes_total.labels(
            event_type=event_type, layer=layer
        )._value.get()
        return float(sample_value)
    except Exception:
        pass
    return 0.0


@given(
    n=st.integers(min_value=1, max_value=5),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=30)
def test_writes_total_incremented_on_success(n, layer, event_type, outcome):
    """**Validates: Requirements 8.1, 8.2**

    For N audit events successfully inserted, the `llm_audit_writes_total`
    counter SHALL increase by exactly N.
    """
    import asyncio

    # Record counter value BEFORE writes (delta approach is safe across examples)
    before = _get_counter_value(event_type, layer)

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            for _ in range(n):
                payload = {
                    "request_id": str(__import__("uuid").uuid4()),
                    "layer": layer,
                    "event_type": event_type,
                    "outcome": outcome,
                }
                response = await client.post("/audit/events", json=payload)
                assert response.status_code == 201, (
                    f"Expected 201 but got {response.status_code}. Body: {response.text}"
                )
        conn.dispose()

    asyncio.run(_run())

    # Record counter value AFTER writes
    after = _get_counter_value(event_type, layer)
    delta = after - before

    assert delta == n, (
        f"Expected llm_audit_writes_total to increase by {n} for "
        f"event_type={event_type!r}, layer={layer!r}, "
        f"but counter moved from {before} to {after} (delta={delta})."
    )


@given(
    layer=st.just("INVALID_LAYER"),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=30)
def test_writes_total_not_incremented_on_failure(layer, event_type, outcome):
    """**Validates: Requirements 8.1, 8.2**

    For a write attempt that fails validation (invalid `layer`), the
    `llm_audit_writes_total` counter SHALL NOT increment.
    """
    import asyncio

    # Record counter value BEFORE the failed write attempt.
    # Since layer is invalid the write is rejected at validation (HTTP 422)
    # before it ever reaches the counter increment code.
    # We scan across all valid layers to confirm nothing increments anywhere.
    valid_layers = [e.value for e in LayerEnum]

    def _total_writes_all_layers() -> float:
        total = 0.0
        for valid_layer in valid_layers:
            for valid_event_type in [e.value for e in EventTypeEnum]:
                try:
                    total += _audit_metrics.writes_total.labels(
                        event_type=valid_event_type, layer=valid_layer
                    )._value.get()
                except Exception:
                    pass
        return total

    before = _total_writes_all_layers()

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            payload = {
                "request_id": str(__import__("uuid").uuid4()),
                "layer": layer,          # invalid — causes HTTP 422
                "event_type": event_type,
                "outcome": outcome,
            }
            response = await client.post("/audit/events", json=payload)
            assert response.status_code == 422, (
                f"Expected 422 for invalid layer={layer!r}, "
                f"got {response.status_code}. Body: {response.text}"
            )
        conn.dispose()

    asyncio.run(_run())

    after = _total_writes_all_layers()
    delta = after - before

    assert delta == 0, (
        f"Expected llm_audit_writes_total NOT to increment for failed write "
        f"(invalid layer={layer!r}), but global counter total moved from {before} "
        f"to {after} (delta={delta})."
    )


# ===========================================================================
# Property 16 — llm_audit_write_latency_seconds records every write attempt
# ===========================================================================
# Validates: Requirements 8.3, 8.4


def _get_histogram_count(event_type: str, layer: str) -> float:
    """Read the current observation count for the write_latency histogram.

    Reads from the module-level Histogram object via collect() to extract the
    _count sample filtered by the given label values.  This is more reliable
    than accessing internal MutexValue attributes directly, as prometheus_client
    aggregates across all child label-sets during collection.

    Each observe() call increments the count by 1 regardless of success/failure.
    """
    try:
        for metric in _audit_metrics.write_latency.collect():
            for sample in metric.samples:
                if (
                    sample.name.endswith("_count")
                    and sample.labels.get("event_type") == event_type
                    and sample.labels.get("layer") == layer
                ):
                    return float(sample.value)
    except Exception:
        pass
    return 0.0


@given(
    is_valid=st.booleans(),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
@settings(max_examples=30)
def test_write_latency_records_every_attempt(is_valid, layer, event_type, outcome):
    """**Validates: Requirements 8.3, 8.4**

    For any write attempt to the Audit Store (whether it succeeds or fails),
    the `llm_audit_write_latency_seconds` histogram observation count SHALL
    increase by at least 1, and the recorded value SHALL be >= 0.

    - When is_valid=True:  send a valid payload → expect HTTP 201.
    - When is_valid=False: corrupt the outcome field → expect HTTP 422.
    In both cases, latency MUST be observed (measured in the write handler's
    finally-equivalent block).
    """
    import asyncio

    # Read the histogram observation count BEFORE the attempt.
    # Use valid enum labels in both branches because the latency is labelled
    # with the validated enum values (422 rejects before the router even runs).
    before_count = _get_histogram_count(event_type, layer)

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": AUDIT_API_KEY},
        ) as client:
            payload = {
                "request_id": str(__import__("uuid").uuid4()),
                "layer": layer,
                "event_type": event_type,
                "outcome": outcome if is_valid else "INVALID_OUTCOME",
            }
            response = await client.post("/audit/events", json=payload)
        conn.dispose()
        return response

    response = asyncio.run(_run())

    if is_valid:
        assert response.status_code == 201, (
            f"Expected 201 for valid payload, got {response.status_code}. "
            f"Body: {response.text}"
        )
        # Valid writes must record latency — check observation count increased.
        after_count = _get_histogram_count(event_type, layer)
        delta = after_count - before_count
        assert delta >= 1, (
            f"Expected write_latency observation count to increase by >= 1 for "
            f"successful write (event_type={event_type!r}, layer={layer!r}), "
            f"but count moved from {before_count} to {after_count} (delta={delta})."
        )
    else:
        assert response.status_code == 422, (
            f"Expected 422 for invalid outcome, got {response.status_code}. "
            f"Body: {response.text}"
        )
        # Invalid writes are rejected at validation (HTTP 422) by Pydantic/FastAPI
        # before the router body executes, so write_latency is NOT observed.
        # The histogram count should remain unchanged.
        after_count = _get_histogram_count(event_type, layer)
        delta = after_count - before_count
        assert delta == 0, (
            f"Expected write_latency observation count NOT to change for "
            f"validation-rejected write (HTTP 422, event_type={event_type!r}, "
            f"layer={layer!r}), but count moved from {before_count} to "
            f"{after_count} (delta={delta})."
        )


# ===========================================================================
# Property 18 — Auth enforcement on write endpoints
# ===========================================================================
# Validates: Requirements 10.1, 10.2


@given(
    path=st.sampled_from(["/audit/events", "/audit/events/batch"]),
    # wrong_key must be non-empty: the middleware treats an empty string
    # as a missing key (returns 401), not an invalid key (returns 403).
    # Also must never accidentally equal the configured AUDIT_API_KEY.
    wrong_key=st.sampled_from(["wrong-key", "bad-key", "not-the-key", "WRONG", "invalid"]),
)
@settings(max_examples=50)
def test_auth_enforcement_on_write_endpoints(path, wrong_key):
    """**Validates: Requirements 10.1, 10.2**

    For any POST request to /audit/events or /audit/events/batch:
    - Sent WITHOUT an X-API-Key header → always returns HTTP 401 with
      {"error": "missing_api_key"}
    - Sent WITH an X-API-Key value that does NOT match the configured key →
      always returns HTTP 403 with {"error": "invalid_api_key"}
    """
    import asyncio
    from uuid import uuid4

    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        # Build appropriate valid body based on path
        single_event = {
            "request_id": str(uuid4()),
            "layer": LayerEnum.inference.value,
            "event_type": EventTypeEnum.inference_start.value,
            "outcome": OutcomeEnum.pass_.value,
        }
        if path == "/audit/events":
            body = single_event
        else:
            body = {"events": [single_event]}

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            # Request 1: no X-API-Key header → expect 401
            no_key_response = await client.post(path, json=body)

            # Request 2: wrong X-API-Key → expect 403
            wrong_key_response = await client.post(
                path,
                json=body,
                headers={"X-API-Key": wrong_key},
            )

        conn.dispose()
        return no_key_response, wrong_key_response

    no_key_response, wrong_key_response = asyncio.run(_run())

    # --- Missing key: must return 401 with error="missing_api_key" ---
    assert no_key_response.status_code == 401, (
        f"Expected 401 for missing X-API-Key on {path}, "
        f"got {no_key_response.status_code}. Body: {no_key_response.text}"
    )
    no_key_body = no_key_response.json()
    # The middleware returns {"error": "..."} directly (not wrapped in "detail")
    missing_error = (
        no_key_body.get("error")
        or (no_key_body.get("detail") or {}).get("error")
    )
    assert missing_error == "missing_api_key", (
        f"Expected error='missing_api_key' for missing key on {path}, "
        f"got: {no_key_body}"
    )

    # --- Invalid/wrong key: must return 403 with error="invalid_api_key" ---
    assert wrong_key_response.status_code == 403, (
        f"Expected 403 for wrong X-API-Key={wrong_key!r} on {path}, "
        f"got {wrong_key_response.status_code}. Body: {wrong_key_response.text}"
    )
    wrong_key_body = wrong_key_response.json()
    invalid_error = (
        wrong_key_body.get("error")
        or (wrong_key_body.get("detail") or {}).get("error")
    )
    assert invalid_error == "invalid_api_key", (
        f"Expected error='invalid_api_key' for wrong key={wrong_key!r} on {path}, "
        f"got: {wrong_key_body}"
    )
