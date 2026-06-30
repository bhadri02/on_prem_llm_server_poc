"""
tests/integration/test_e2e.py — End-to-end integration validation for the Audit Store.

Covers:
  - test_full_request_lifecycle     : Six-layer write + GET by request_id, summary, and
                                       user_id filter all behave correctly.
  - test_prometheus_metrics_endpoint: /metrics returns valid Prometheus text format with
                                       the expected metric names after a write.
  - test_auth_boundary              : GET endpoints require no key; POST endpoints enforce
                                       401 (missing) and 403 (wrong) key correctly.
  - test_structured_log_output      : Every log line emitted during a write+query cycle is
                                       valid single-line JSON with 'timestamp' and 'level'.
"""

import io
import json
import logging
import uuid

import httpx
import pytest
import pytest_asyncio

from audit_store.logging_config import JSONFormatter
from audit_store.metrics_app import metrics_app

# ---------------------------------------------------------------------------
# 20.1  Full request lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_request_lifecycle(app, async_client):
    """POST one event per layer, then verify GET by request_id, summary, and user filter."""
    request_id = str(uuid.uuid4())
    user_id = "e2e-user"

    # One event per LayerEnum value with staggered timestamps.
    layers = [
        ("api_gateway",  "request_received", "pass"),
        ("security",     "auth_pass",         "pass"),
        ("router",       "request_received",  "pass"),
        ("cache",        "cache_hit",          "pass"),
        ("inference",    "inference_start",    "pass"),
        ("agent",        "response_sent",      "pass"),
    ]

    base_ts = "2025-01-01T00:00:0{}Z"
    for i, (layer, event_type, outcome) in enumerate(layers):
        payload = {
            "request_id": request_id,
            "user_id": user_id,
            "layer": layer,
            "event_type": event_type,
            "outcome": outcome,
            "timestamp_utc": base_ts.format(i),
        }
        resp = await async_client.post("/audit/events", json=payload)
        assert resp.status_code == 201, (
            f"Insert failed for layer={layer}: {resp.text}"
        )

    # --- GET /audit/requests/{request_id} ---
    resp = await async_client.get(f"/audit/requests/{request_id}")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 6, f"Expected 6 events, got {len(events)}"

    # Ascending timestamp order
    ts_values = [e["timestamp_utc"] for e in events]
    assert ts_values == sorted(ts_values), "Events not returned in ascending timestamp order"

    # --- GET /audit/summary ---
    resp = await async_client.get("/audit/summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["total_events"] >= 6, (
        f"total_events={summary['total_events']} < 6"
    )

    expected_layers = {"api_gateway", "security", "router", "cache", "inference", "agent"}
    by_layer = summary["by_layer"]
    for layer_name in expected_layers:
        assert layer_name in by_layer, f"Layer {layer_name!r} missing from by_layer"
        assert by_layer[layer_name] >= 1, (
            f"Expected count >= 1 for layer {layer_name!r}, got {by_layer[layer_name]}"
        )

    # --- GET /audit/events?user_id=e2e-user ---
    resp = await async_client.get(f"/audit/events?user_id={user_id}")
    assert resp.status_code == 200
    user_events = resp.json()
    assert len(user_events) >= 6, f"Expected at least 6 events for user, got {len(user_events)}"
    for event in user_events:
        assert event["user_id"] == user_id, (
            f"user_id mismatch: expected {user_id!r}, got {event['user_id']!r}"
        )


# ---------------------------------------------------------------------------
# 20.2  Prometheus metrics endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint(app, async_client):
    """After at least one write, /metrics returns valid Prometheus text with our metric names."""
    from prometheus_client import REGISTRY
    import audit_store.metrics as _metrics_module

    # The reset_prometheus autouse fixture unregisters our metrics before this
    # test runs.  Re-register the current module-level collectors so the
    # default registry (and therefore metrics_app) can expose them.
    for collector in (_metrics_module.writes_total, _metrics_module.write_latency):
        try:
            REGISTRY.register(collector)
        except Exception:
            pass  # Already registered — safe to continue.

    # Write one event so the counters have data
    payload = {
        "request_id": str(uuid.uuid4()),
        "layer": "api_gateway",
        "event_type": "request_received",
        "outcome": "pass",
    }
    resp = await async_client.post("/audit/events", json=payload)
    assert resp.status_code == 201, f"Write failed: {resp.text}"

    # Scrape the metrics app with a fresh client — metrics_app is the separate Starlette app.
    # Mount("/metrics", ...) redirects bare /metrics to /metrics/ with a 307;
    # follow redirects so we land on the actual handler.
    transport = httpx.ASGITransport(app=metrics_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testmetrics",
        follow_redirects=True,
    ) as mc:
        resp = await mc.get("/metrics")

    assert resp.status_code == 200, f"Expected 200 from /metrics, got {resp.status_code}"

    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type, (
        f"Expected text/plain in Content-Type, got: {content_type!r}"
    )
    assert "version=0.0.4" in content_type, (
        f"Expected version=0.0.4 in Content-Type, got: {content_type!r}"
    )

    body = resp.text
    assert "llm_audit_writes_total" in body, (
        "llm_audit_writes_total not found in /metrics output"
    )
    assert "llm_audit_write_latency_seconds" in body, (
        "llm_audit_write_latency_seconds not found in /metrics output"
    )


# ---------------------------------------------------------------------------
# 20.3  Auth boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_boundary(app, async_client):
    """GET endpoints need no key; POST without/wrong key returns 401/403."""
    minimal_event = {
        "request_id": str(uuid.uuid4()),
        "layer": "api_gateway",
        "event_type": "request_received",
        "outcome": "pass",
    }

    # Build a client with no default auth header for unauthenticated tests
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as noauth_client:

        # --- GET endpoints require no auth ---
        resp = await noauth_client.get("/audit/events")
        assert resp.status_code not in (401, 403), (
            f"GET /audit/events should not require auth, got {resp.status_code}"
        )

        resp = await noauth_client.get("/audit/summary")
        assert resp.status_code not in (401, 403), (
            f"GET /audit/summary should not require auth, got {resp.status_code}"
        )

        resp = await noauth_client.get("/health")
        assert resp.status_code not in (401, 403), (
            f"GET /health should not require auth, got {resp.status_code}"
        )

        # --- POST with no key → 401 ---
        resp = await noauth_client.post("/audit/events", json=minimal_event)
        assert resp.status_code == 401, (
            f"Expected 401 without X-API-Key, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error") == "missing_api_key", (
            f"Expected error='missing_api_key', got: {body}"
        )

        # --- POST with wrong key → 403 ---
        resp = await noauth_client.post(
            "/audit/events",
            json={**minimal_event, "request_id": str(uuid.uuid4())},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 with wrong X-API-Key, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error") == "invalid_api_key", (
            f"Expected error='invalid_api_key', got: {body}"
        )

    # --- POST with correct key → 201 ---
    resp = await async_client.post(
        "/audit/events",
        json={**minimal_event, "request_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 201, (
        f"Expected 201 with valid X-API-Key, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# 20.4  Structured log output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_structured_log_output(app):
    """Every log line emitted during write+query is valid single-line JSON with required fields."""
    # Target the logger used by the write router
    target_logger = logging.getLogger("audit_store.routers.write")

    # Capture into a StringIO buffer via a fresh handler
    buffer = io.StringIO()
    capture_handler = logging.StreamHandler(buffer)
    capture_handler.setFormatter(JSONFormatter())
    capture_handler.setLevel(logging.DEBUG)

    # Temporarily replace the logger's handlers
    original_handlers = target_logger.handlers[:]
    original_propagate = target_logger.propagate
    target_logger.handlers = [capture_handler]
    target_logger.propagate = False

    try:
        # Perform write + query operations inline (no async_client fixture)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as client:
            req_id = str(uuid.uuid4())

            # Write 2 events
            for i in range(2):
                payload = {
                    "request_id": req_id,
                    "layer": "api_gateway",
                    "event_type": "request_received",
                    "outcome": "pass",
                    "timestamp_utc": f"2025-06-01T10:00:0{i}Z",
                }
                resp = await client.post("/audit/events", json=payload)
                assert resp.status_code == 201, (
                    f"Write {i} failed: {resp.text}"
                )

            # Query back
            resp = await client.get(f"/audit/requests/{req_id}")
            assert resp.status_code == 200

    finally:
        # Always restore original handlers
        target_logger.handlers = original_handlers
        target_logger.propagate = original_propagate

    # Validate captured log lines
    log_output = buffer.getvalue()
    lines = [line for line in log_output.splitlines() if line.strip()]

    assert len(lines) > 0, "No log lines were captured during write+query cycle"

    for line in lines:
        # Must be valid JSON
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"Log line is not valid JSON: {exc}\nLine: {line!r}"
            ) from exc

        # Must have 'timestamp' and 'level' fields
        assert "timestamp" in parsed, (
            f"'timestamp' field missing from log line: {line!r}"
        )
        assert "level" in parsed, (
            f"'level' field missing from log line: {line!r}"
        )

        # Must be truly single-line (no embedded newlines)
        assert "\n" not in line, (
            f"Log line contains embedded newline: {line!r}"
        )
