"""
tests/integration/test_health.py — Integration tests for GET /health
on the Security & Governance Layer.

Covers:
  - test_health_ok                  : patterns loaded + PII disabled → 200 ok
  - test_health_returns_patterns_count : patterns_loaded in body matches actual count
  - test_health_presidio_unavailable: pii_enabled=True but analyzer=None → 503
  - test_health_no_patterns         : patterns=[] → 503 no_patterns_loaded
  - test_health_both_degraded       : pii_enabled=True, analyzer=None,
                                      patterns=[] → 503 presidio_unavailable
                                      (presidio takes priority)
  - test_health_no_auth_required    : no X-API-Key header needed → 200
"""

import re

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(app):
    """Return a context-manager async client using ASGITransport (no lifespan)."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


def _populate_state(app, patterns=None, pii_enabled=False, analyzer=None):
    """Directly set the attributes that the health endpoint reads from app.state."""
    mock_settings = MagicMock()
    mock_settings.pii_enabled = pii_enabled

    if patterns is None:
        # Default: 3 compiled regex patterns
        patterns = [
            re.compile("ignore previous instructions", re.IGNORECASE),
            re.compile("you are now", re.IGNORECASE),
            re.compile("pretend you are", re.IGNORECASE),
        ]

    app.state.settings = mock_settings
    app.state.patterns = patterns
    app.state.analyzer = analyzer
    app.state.anonymizer = None
    app.state.blocklist = []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_ok(security_test_app):
    """Patterns loaded + PII disabled → HTTP 200 with status ok."""
    _populate_state(security_test_app, pii_enabled=False, analyzer=None)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["pii_enabled"] is False
    assert body["patterns_loaded"] > 0


@pytest.mark.asyncio
async def test_health_returns_patterns_count(security_test_app):
    """patterns_loaded in the response matches the number of loaded patterns."""
    patterns = [
        re.compile("pattern one", re.IGNORECASE),
        re.compile("pattern two", re.IGNORECASE),
    ]
    _populate_state(security_test_app, patterns=patterns)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["patterns_loaded"] == 2


@pytest.mark.asyncio
async def test_health_presidio_unavailable(security_test_app):
    """pii_enabled=True + analyzer=None → HTTP 503 presidio_unavailable."""
    _populate_state(security_test_app, pii_enabled=True, analyzer=None)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reason"] == "presidio_unavailable"


@pytest.mark.asyncio
async def test_health_no_patterns_loaded(security_test_app):
    """patterns=[] → HTTP 503 no_patterns_loaded (PII disabled, so presidio OK)."""
    _populate_state(security_test_app, patterns=[], pii_enabled=False)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reason"] == "no_patterns_loaded"


@pytest.mark.asyncio
async def test_health_both_degraded_presidio_takes_priority(security_test_app):
    """pii_enabled=True, analyzer=None, patterns=[] → presidio_unavailable wins."""
    _populate_state(security_test_app, patterns=[], pii_enabled=True, analyzer=None)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["reason"] == "presidio_unavailable"


@pytest.mark.asyncio
async def test_health_no_auth_required(security_test_app):
    """GET /health must return 200 without an X-API-Key header."""
    _populate_state(security_test_app, pii_enabled=False)

    async with _make_client(security_test_app) as client:
        # Explicitly omit any auth header
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_no_auth_required_with_header_still_ok(security_test_app):
    """Providing an X-API-Key should not break the health endpoint."""
    _populate_state(security_test_app, pii_enabled=False)

    async with _make_client(security_test_app) as client:
        response = await client.get("/health", headers={"X-API-Key": "any-key"})

    assert response.status_code == 200
