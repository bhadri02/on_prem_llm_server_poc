"""
tests/integration/test_health.py — Integration tests for GET /health.

Covers:
  - test_health_ok       : Normal in-memory DB returns 200 {"status": "ok", "db": "connected"}
  - test_health_degraded : Closed/broken connection returns 503 {"status": "degraded", "db": "unreachable"}
"""

import pytest

from audit_store.database import get_connection


@pytest.mark.asyncio
async def test_health_ok(async_client):
    """GET /health with a live in-memory DB returns HTTP 200 with the expected body."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}


@pytest.mark.asyncio
async def test_health_degraded(app, async_client):
    """GET /health with a closed connection returns HTTP 503 with degraded status."""
    original_conn = app.state.conn

    # Create a new connection then immediately close it to simulate unreachable DB.
    broken_conn = get_connection(":memory:")
    broken_conn.close()
    app.state.conn = broken_conn

    try:
        response = await async_client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["db"] == "unreachable"
    finally:
        # Restore the original working connection regardless of test outcome.
        app.state.conn = original_conn
