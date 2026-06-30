"""
Property-based tests for the Audit Store query endpoints.

Properties covered (tasks 14.2–14.7):
  - Property  9: Request lifecycle trace is ordered correctly
  - Property 10: JSON round-trip for pii_actions and policy_decisions
  - Property 12: Filter query results satisfy all supplied conditions conjunctively
  - Property 13: Filter query results are ordered descending by timestamp, capped at 1000
  - Property 14: Summary counts form a consistent totals invariant
  - Property 19: GET endpoints require no auth (no 401/403 on unauthenticated GET)

All tests are synchronous Hypothesis tests that spin up a fresh in-memory
FastAPI app per example via asyncio.run(), matching the pattern established
in test_write_properties.py.
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import httpx
import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.database import InMemoryExampleDatabase

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# Must be done before any @given-decorated functions are defined.
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, database=InMemoryExampleDatabase())
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from audit_store.models import (
    UUID4_RE,
    LayerEnum,
    EventTypeEnum,
    OutcomeEnum,
)
from audit_store.database import init_schema, get_connection
from audit_store.main import create_app

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------
AUDIT_API_KEY = "test-key"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Build a fresh in-memory FastAPI app for one Hypothesis example.

    Mirrors the helper in test_write_properties.py so each property-based
    example starts with a completely clean database.
    """

    @asynccontextmanager
    async def _noop_lifespan(application):
        yield

    application = create_app()
    application.router.lifespan_context = _noop_lifespan

    conn = get_connection(":memory:")
    init_schema(conn)

    class _TestSettings:
        audit_api_key: str = AUDIT_API_KEY
        db_path: str = ":memory:"

    application.state.conn = conn
    application.state.settings = _TestSettings()
    return application, conn


def _make_client(application):
    """Return an async HTTP client wired to *application* via ASGITransport."""
    transport = httpx.ASGITransport(app=application)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": AUDIT_API_KEY},
    )


def _valid_event_payload(request_id: str, **overrides) -> dict:
    """Return a minimal valid POST /audit/events payload for *request_id*."""
    payload = {
        "request_id": request_id,
        "layer": LayerEnum.inference.value,
        "event_type": EventTypeEnum.inference_start.value,
        "outcome": OutcomeEnum.pass_.value,
    }
    payload.update(overrides)
    return payload


def valid_event_strategy() -> st.SearchStrategy:
    """Hypothesis strategy that generates minimal valid event payloads (dicts)."""
    return st.fixed_dictionaries({
        "request_id": st.uuids(version=4).map(str),
        "layer": st.sampled_from([e.value for e in LayerEnum]),
        "event_type": st.sampled_from([e.value for e in EventTypeEnum]),
        "outcome": st.sampled_from([e.value for e in OutcomeEnum]),
    })


# ===========================================================================
# Property 9 — Request lifecycle trace ordering
# ===========================================================================
# Validates: Requirement 3.1


@given(
    events=st.lists(
        st.fixed_dictionaries({
            "layer": st.sampled_from([e.value for e in LayerEnum]),
            "event_type": st.sampled_from([e.value for e in EventTypeEnum]),
            "outcome": st.sampled_from([e.value for e in OutcomeEnum]),
            # Generate distinct ISO-8601 UTC timestamps by building from
            # a base epoch offset (seconds since 2020-01-01) so ordering
            # is deterministic and collisions are possible (testing tie-breaking).
            "offset_seconds": st.integers(min_value=0, max_value=3600),
        }),
        min_size=2,
        max_size=10,
    )
)
@settings(max_examples=50)
def test_request_lifecycle_trace_ordering(events):
    """**Validates: Requirement 3.1**

    For any request_id with N >= 2 associated audit events stored in the
    database, GET /audit/requests/{request_id} SHALL return a JSON array of
    exactly N events ordered by timestamp_utc ascending; when two events
    share the same timestamp_utc they SHALL be secondarily ordered by
    audit_id ascending.
    """
    request_id = str(uuid.uuid4())

    # Build the base datetime (2020-01-01 UTC) to anchor offsets.
    base_dt = datetime(2020, 1, 1, tzinfo=timezone.utc)

    async def _run():
        application, conn = _make_app()

        # Assign shared request_id and deterministic timestamps.
        payloads = []
        for ev in events:
            ts = (base_dt + timedelta(seconds=ev["offset_seconds"])).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            payloads.append({
                "request_id": request_id,
                "layer": ev["layer"],
                "event_type": ev["event_type"],
                "outcome": ev["outcome"],
                "timestamp_utc": ts,
            })

        async with _make_client(application) as client:
            # Insert all events.
            for payload in payloads:
                resp = await client.post("/audit/events", json=payload)
                assert resp.status_code == 201, (
                    f"Insert failed with {resp.status_code}: {resp.text}"
                )

            # Query back.
            resp = await client.get(f"/audit/requests/{request_id}")

        conn.close()
        return resp

    resp = asyncio.run(_run())

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.text}"
    )
    result = resp.json()

    # Correct count.
    assert len(result) == len(events), (
        f"Expected {len(events)} events, got {len(result)}"
    )

    # Sorted by (timestamp_utc ASC, audit_id ASC).
    sort_keys = [(r["timestamp_utc"], r["audit_id"]) for r in result]
    assert sort_keys == sorted(sort_keys), (
        f"Events not sorted by (timestamp_utc, audit_id) ASC. "
        f"Keys: {sort_keys}"
    )


# ===========================================================================
# Property 10 — JSON round-trip for pii_actions and policy_decisions
# ===========================================================================
# Validates: Requirements 3.4, 7.3


@given(
    pii_actions=st.lists(st.text(min_size=0, max_size=50), max_size=10),
    policy_decisions=st.lists(
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.text(min_size=0, max_size=50),
            max_size=5,
        ),
        max_size=5,
    ),
)
@settings(max_examples=50)
def test_json_round_trip_pii_and_policy(pii_actions, policy_decisions):
    """**Validates: Requirements 3.4, 7.3**

    For any audit event written with arbitrary JSON-serialisable arrays in
    pii_actions and policy_decisions, querying that event back via
    GET /audit/requests/{request_id} SHALL return those fields as native JSON
    arrays equal to the originally submitted values.
    """
    request_id = str(uuid.uuid4())

    async def _run():
        application, conn = _make_app()

        payload = _valid_event_payload(
            request_id,
            pii_actions=pii_actions,
            policy_decisions=policy_decisions,
        )

        async with _make_client(application) as client:
            insert_resp = await client.post("/audit/events", json=payload)
            assert insert_resp.status_code == 201, (
                f"Insert failed: {insert_resp.status_code} {insert_resp.text}"
            )

            query_resp = await client.get(f"/audit/requests/{request_id}")

        conn.close()
        return query_resp

    query_resp = asyncio.run(_run())

    assert query_resp.status_code == 200, (
        f"Expected 200, got {query_resp.status_code}. Body: {query_resp.text}"
    )
    results = query_resp.json()
    assert len(results) == 1, f"Expected 1 event, got {len(results)}"

    returned_event = results[0]

    assert returned_event["pii_actions"] == pii_actions, (
        f"pii_actions round-trip failed.\n"
        f"  Sent:     {pii_actions!r}\n"
        f"  Returned: {returned_event['pii_actions']!r}"
    )
    assert returned_event["policy_decisions"] == policy_decisions, (
        f"policy_decisions round-trip failed.\n"
        f"  Sent:     {policy_decisions!r}\n"
        f"  Returned: {returned_event['policy_decisions']!r}"
    )


# ===========================================================================
# Property 12 — Filter query conjunctive correctness
# ===========================================================================
# Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5


# Strategy for distinct user_ids used in the pool
_USER_ID_STRATEGY = st.sampled_from(["alice", "bob", "carol", "dave", "eve"])

# Base datetime for generating timestamps in the pool
_POOL_BASE = datetime(2023, 6, 1, tzinfo=timezone.utc)


def _pool_event_strategy():
    """Generate a single event payload for the filter-correctness pool."""
    return st.fixed_dictionaries({
        "user_id": _USER_ID_STRATEGY,
        "layer": st.sampled_from([e.value for e in LayerEnum]),
        "event_type": st.sampled_from([e.value for e in EventTypeEnum]),
        "outcome": st.sampled_from([e.value for e in OutcomeEnum]),
        # offset 0..86400 seconds (24 hours) from base
        "offset_seconds": st.integers(min_value=0, max_value=86400),
    })


@given(
    pool=st.lists(_pool_event_strategy(), min_size=1, max_size=20),
    apply_user_id=st.booleans(),
    apply_event_type=st.booleans(),
    apply_time_range=st.booleans(),
)
@settings(max_examples=30)
def test_filter_query_conjunctive_correctness(
    pool, apply_user_id, apply_event_type, apply_time_range
):
    """**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

    For any combination of filter parameters (user_id, event_type, from, to)
    supplied to GET /audit/events, every event in the returned JSON array SHALL
    satisfy ALL supplied filter conditions simultaneously (AND logic).
    """
    # Pick filter values from the pool to ensure at least some events pass.
    filter_user_id = pool[0]["user_id"] if apply_user_id else None
    filter_event_type = pool[0]["event_type"] if apply_event_type else None

    # Time range: pick a 12-hour window around the midpoint of the pool.
    from_str = None
    to_str = None
    if apply_time_range:
        mid_offset = 43200  # 12 h
        window_start = _POOL_BASE + timedelta(seconds=mid_offset - 21600)
        window_end = _POOL_BASE + timedelta(seconds=mid_offset + 21600)
        from_str = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_str = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _run():
        application, conn = _make_app()
        request_id = str(uuid.uuid4())

        # Build and insert pool events.
        payloads = []
        for ev in pool:
            ts = (_POOL_BASE + timedelta(seconds=ev["offset_seconds"])).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            payloads.append({
                "request_id": request_id,
                "layer": ev["layer"],
                "event_type": ev["event_type"],
                "outcome": ev["outcome"],
                "user_id": ev["user_id"],
                "timestamp_utc": ts,
            })

        async with _make_client(application) as client:
            for payload in payloads:
                resp = await client.post("/audit/events", json=payload)
                assert resp.status_code == 201, (
                    f"Insert failed: {resp.status_code} {resp.text}"
                )

            # Build query params.
            params = {}
            if filter_user_id is not None:
                params["user_id"] = filter_user_id
            if filter_event_type is not None:
                params["event_type"] = filter_event_type
            if from_str is not None:
                params["from"] = from_str
            if to_str is not None:
                params["to"] = to_str

            query_resp = await client.get("/audit/events", params=params)

        conn.close()
        return query_resp, from_str, to_str

    query_resp, from_str, to_str = asyncio.run(_run())

    assert query_resp.status_code == 200, (
        f"Expected 200, got {query_resp.status_code}. Body: {query_resp.text}"
    )
    results = query_resp.json()

    # Every returned event must satisfy all applied filters.
    for ev in results:
        if filter_user_id is not None:
            assert ev["user_id"] == filter_user_id, (
                f"user_id filter violated: expected {filter_user_id!r}, "
                f"got {ev['user_id']!r}"
            )
        if filter_event_type is not None:
            assert ev["event_type"] == filter_event_type, (
                f"event_type filter violated: expected {filter_event_type!r}, "
                f"got {ev['event_type']!r}"
            )
        if from_str is not None:
            assert ev["timestamp_utc"] >= from_str, (
                f"from filter violated: {ev['timestamp_utc']!r} < {from_str!r}"
            )
        if to_str is not None:
            assert ev["timestamp_utc"] <= to_str, (
                f"to filter violated: {ev['timestamp_utc']!r} > {to_str!r}"
            )


# ===========================================================================
# Property 13 — Filter query ordering and limit
# ===========================================================================
# Validates: Requirement 4.6


@given(
    events=st.lists(valid_event_strategy(), min_size=0, max_size=50),
)
@settings(max_examples=50)
def test_filter_query_ordering_and_limit(events):
    """**Validates: Requirement 4.6**

    For any query to GET /audit/events (with any combination of valid filter
    parameters), the returned array SHALL have at most 1000 elements and SHALL
    be ordered by timestamp_utc descending.
    """
    async def _run():
        application, conn = _make_app()

        async with _make_client(application) as client:
            # Insert all events.
            for ev in events:
                resp = await client.post("/audit/events", json=ev)
                assert resp.status_code == 201, (
                    f"Insert failed: {resp.status_code} {resp.text}"
                )

            # Query with no filters.
            query_resp = await client.get("/audit/events")

        conn.close()
        return query_resp

    query_resp = asyncio.run(_run())

    assert query_resp.status_code == 200, (
        f"Expected 200, got {query_resp.status_code}. Body: {query_resp.text}"
    )
    results = query_resp.json()

    # At most 1000 records.
    assert len(results) <= 1000, (
        f"Expected <= 1000 results, got {len(results)}"
    )

    # Ordered by timestamp_utc descending.
    timestamps = [r["timestamp_utc"] for r in results]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"Results not sorted by timestamp_utc DESC. Timestamps: {timestamps}"
    )


# ===========================================================================
# Property 14 — Summary totals invariant
# ===========================================================================
# Validates: Requirements 5.1, 5.2, 5.3, 5.4


@given(
    events=st.lists(valid_event_strategy(), min_size=0, max_size=50),
)
@settings(max_examples=50)
def test_summary_totals_invariant(events):
    """**Validates: Requirements 5.1, 5.2, 5.3, 5.4**

    For any time range (or no range) applied to GET /audit/summary, the
    response SHALL satisfy:
      sum(by_outcome.values()) == total_events
      sum(by_layer.values())   == total_events
    """
    async def _run():
        application, conn = _make_app()

        async with _make_client(application) as client:
            # Insert all events.
            for ev in events:
                resp = await client.post("/audit/events", json=ev)
                assert resp.status_code == 201, (
                    f"Insert failed: {resp.status_code} {resp.text}"
                )

            # Call summary with no time range.
            summary_resp = await client.get("/audit/summary")

        conn.close()
        return summary_resp

    summary_resp = asyncio.run(_run())

    assert summary_resp.status_code == 200, (
        f"Expected 200, got {summary_resp.status_code}. Body: {summary_resp.text}"
    )
    body = summary_resp.json()

    total_events = body["total_events"]
    by_outcome = body["by_outcome"]
    by_layer = body["by_layer"]

    outcome_sum = sum(by_outcome.values())
    layer_sum = sum(by_layer.values())

    assert outcome_sum == total_events, (
        f"sum(by_outcome.values())={outcome_sum} != total_events={total_events}. "
        f"by_outcome={by_outcome}"
    )
    assert layer_sum == total_events, (
        f"sum(by_layer.values())={layer_sum} != total_events={total_events}. "
        f"by_layer={by_layer}"
    )


# ===========================================================================
# Property 19 — GET endpoints require no authentication
# ===========================================================================
# Validates: Requirements 10.6, 6.4


@given(
    path=st.sampled_from(["/audit/events", "/audit/summary", "/health"]),
)
@settings(max_examples=50)
def test_get_endpoints_no_auth_required(path):
    """**Validates: Requirements 10.6, 6.4**

    For any GET request to /audit/events, /audit/summary, or /health sent
    WITHOUT an X-API-Key header, the response SHALL NOT be 401 or 403.
    GET endpoints must process requests without checking for the header.
    """
    async def _run():
        application, conn = _make_app()
        transport = httpx.ASGITransport(app=application)

        # Deliberately omit the X-API-Key header.
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            # No X-API-Key header set.
        ) as client:
            resp = await client.get(path)

        conn.close()
        return resp

    resp = asyncio.run(_run())

    assert resp.status_code not in {401, 403}, (
        f"GET {path} without X-API-Key returned {resp.status_code} "
        f"(expected anything other than 401/403). Body: {resp.text}"
    )
