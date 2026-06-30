"""
tests/integration/test_lifecycle.py — Full request lifecycle integration tests.

Covers:
  - test_full_request_lifecycle       : POST 6 events (one per layer) then GET by request_id;
                                        verifies count, ordering, and list field types.
  - test_pii_and_policy_roundtrip     : Non-empty pii_actions / policy_decisions survive
                                        a write → read round-trip as native lists.
  - test_batch_rollback_on_duplicate  : A batch with a duplicate audit_id rolls back
                                        entirely; the DB row count stays at 1.
"""

import uuid
import pytest


# ---------------------------------------------------------------------------
# test_full_request_lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_request_lifecycle(async_client):
    """POST one event per layer then verify ordering and field types via GET."""
    request_id = str(uuid.uuid4())
    layers = ["api_gateway", "security", "router", "cache", "inference", "agent"]

    # Use strictly increasing timestamps to guarantee ascending order.
    timestamps = [f"2024-01-01T00:00:0{i}.000Z" for i in range(6)]

    for layer, ts in zip(layers, timestamps):
        payload = {
            "request_id": request_id,
            "layer": layer,
            "event_type": "request_received",
            "outcome": "pass",
            "timestamp_utc": ts,
            "pii_actions": [],
            "policy_decisions": [],
        }
        resp = await async_client.post("/audit/events", json=payload)
        assert resp.status_code == 201, f"Insert failed for layer={layer}: {resp.text}"

    resp = await async_client.get(f"/audit/requests/{request_id}")
    assert resp.status_code == 200

    events = resp.json()
    assert len(events) == 6, f"Expected 6 events, got {len(events)}"

    # Events must be ordered by timestamp_utc ascending.
    ts_values = [e["timestamp_utc"] for e in events]
    assert ts_values == sorted(ts_values), "Events are not in ascending timestamp order"

    # pii_actions and policy_decisions must be native lists, not raw strings.
    for event in events:
        assert isinstance(event["pii_actions"], list), (
            f"pii_actions is not a list: {type(event['pii_actions'])}"
        )
        assert isinstance(event["policy_decisions"], list), (
            f"policy_decisions is not a list: {type(event['policy_decisions'])}"
        )


# ---------------------------------------------------------------------------
# test_pii_and_policy_roundtrip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pii_and_policy_roundtrip(async_client):
    """Non-empty pii_actions and policy_decisions survive write → read as native lists."""
    request_id = str(uuid.uuid4())

    pii_actions = ["mask_email", "mask_phone"]
    policy_decisions = ["allow_inference", "flag_review"]

    payload = {
        "request_id": request_id,
        "layer": "security",
        "event_type": "auth_pass",
        "outcome": "pass",
        "timestamp_utc": "2024-06-15T12:00:00.000Z",
        "pii_actions": pii_actions,
        "policy_decisions": policy_decisions,
    }
    resp = await async_client.post("/audit/events", json=payload)
    assert resp.status_code == 201, f"Insert failed: {resp.text}"

    resp = await async_client.get(f"/audit/requests/{request_id}")
    assert resp.status_code == 200

    events = resp.json()
    assert len(events) == 1

    returned = events[0]
    assert isinstance(returned["pii_actions"], list)
    assert returned["pii_actions"] == pii_actions

    assert isinstance(returned["policy_decisions"], list)
    assert returned["policy_decisions"] == policy_decisions


# ---------------------------------------------------------------------------
# test_batch_rollback_on_duplicate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_rollback_on_duplicate(async_client):
    """A batch containing a duplicate audit_id is fully rolled back (HTTP 409)."""
    request_id = str(uuid.uuid4())
    known_audit_id = str(uuid.uuid4())

    # Pre-insert the event that owns known_audit_id.
    payload = {
        "audit_id": known_audit_id,
        "request_id": request_id,
        "layer": "api_gateway",
        "event_type": "request_received",
        "outcome": "pass",
    }
    resp = await async_client.post("/audit/events", json=payload)
    assert resp.status_code == 201, f"Pre-insert failed: {resp.text}"

    # Submit a batch where the middle event re-uses the known audit_id.
    batch = {
        "events": [
            {
                "request_id": request_id,
                "layer": "security",
                "event_type": "auth_pass",
                "outcome": "pass",
            },
            {
                "audit_id": known_audit_id,           # ← duplicate — must cause 409
                "request_id": request_id,
                "layer": "router",
                "event_type": "cache_hit",
                "outcome": "pass",
            },
            {
                "request_id": request_id,
                "layer": "cache",
                "event_type": "cache_hit",
                "outcome": "pass",
            },
        ]
    }
    resp = await async_client.post("/audit/events/batch", json=batch)
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    # The entire batch must have been rolled back — only the original event remains.
    resp = await async_client.get(f"/audit/requests/{request_id}")
    assert resp.status_code == 200

    events = resp.json()
    assert len(events) == 1, f"Expected 1 event after rollback, got {len(events)}"
    assert events[0]["audit_id"] == known_audit_id
