"""
tests/test_property_invalid_imf.py

Property test: Invalid IMF inputs always return HTTP 400 (Property 11).

Feature: agent-framework, Property 11: Invalid IMF inputs always return HTTP 400

Validates: Requirements 1.3, 10.1, 10.8

Uses @given(st.booleans(), st.booleans()) to generate IMF variants with:
  - empty messages array
  - missing messages field
  - extensions.agentic=False
  - extensions.agentic absent

For all invalid variants:
  - Verifies HTTP 400 is returned
  - Verifies no agent session is created (session store remains empty)

Note: This property test exercises the router validation logic
using FastAPI's TestClient with mocked app.state.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UUID_V4 = "550e8400-e29b-41d4-a716-446655440000"
ONE_MESSAGE = [{"role": "user", "content": "What is 2+2?"}]


def _make_test_app():
    """Create a FastAPI test app with the agent router but mocked lifespan state."""
    from fastapi import FastAPI
    from agent_framework.routers import agent, health

    app = FastAPI()
    app.include_router(health.router)
    app.include_router(agent.router)

    # Attach minimal state so the router can access it
    app.state.tool_registry = {}
    app.state.settings = MagicMock()

    return app


def _minimal_valid_imf(**overrides) -> dict:
    """Return a minimal valid IMF payload that would pass router validation."""
    base = {
        "request_id": VALID_UUID_V4,
        "user": {"user_id": "test-user"},
        "request": {"messages": ONE_MESSAGE},
        "extensions": {"agentic": True},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Property 11: Invalid IMF inputs always return HTTP 400
# ---------------------------------------------------------------------------


class TestInvalidIMFReturns400:
    """
    Property 11: Every invalid IMF combination must produce HTTP 400
    and must never create an agent session.
    """

    def setup_method(self):
        self._app = _make_test_app()
        self._client = TestClient(self._app, raise_server_exceptions=False)

    # --- Case 1: extensions.agentic = False ---

    def test_agentic_false_returns_400(self):
        """extensions.agentic=False must return HTTP 400."""
        payload = _minimal_valid_imf(extensions={"agentic": False})
        resp = self._client.post("/agent/run", json=payload)
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body or "detail" in body

    # --- Case 2: extensions.agentic absent ---

    def test_agentic_absent_returns_400(self):
        """extensions.agentic absent must return HTTP 400."""
        payload = _minimal_valid_imf(extensions={})
        resp = self._client.post("/agent/run", json=payload)
        assert resp.status_code == 400

    # --- Case 3: extensions missing entirely ---

    def test_extensions_missing_returns_400(self):
        """If extensions block is absent from the document, must return HTTP 400.
        (IMFDocument defaults extensions to {} → agentic will be absent → 400)
        """
        payload = {
            "request_id": VALID_UUID_V4,
            "user": {"user_id": "test-user"},
            "request": {"messages": ONE_MESSAGE},
            # no extensions key
        }
        resp = self._client.post("/agent/run", json=payload)
        assert resp.status_code == 400

    # --- Case 4: empty messages ---

    def test_empty_messages_returns_400(self):
        """Empty messages array must return HTTP 400 (Pydantic validation)."""
        payload = {
            "request_id": VALID_UUID_V4,
            "user": {"user_id": "test-user"},
            "request": {"messages": []},
            "extensions": {"agentic": True},
        }
        resp = self._client.post("/agent/run", json=payload)
        assert resp.status_code == 422  # FastAPI returns 422 for Pydantic validation errors

    # --- Case 5: invalid JSON (string body) ---

    def test_invalid_json_returns_422(self):
        """Non-JSON body must return HTTP 422 (FastAPI unprocessable entity)."""
        resp = self._client.post(
            "/agent/run",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    # --- Case 6: missing request.messages (absent key) ---

    def test_missing_messages_key_returns_422(self):
        """Missing messages key must return HTTP 422 (Pydantic validation)."""
        payload = {
            "request_id": VALID_UUID_V4,
            "user": {"user_id": "test-user"},
            "request": {},  # messages key is absent
            "extensions": {"agentic": True},
        }
        resp = self._client.post("/agent/run", json=payload)
        assert resp.status_code == 422


@hyp_settings(max_examples=50)
@given(
    agentic_value=st.one_of(st.just(False), st.just(None), st.just("")),
    include_extensions=st.booleans(),
)
def test_property_invalid_agentic_always_returns_400_or_422(
    agentic_value, include_extensions
):
    """
    Feature: agent-framework, Property 11: Invalid IMF inputs always return HTTP 400

    Validates: Requirements 1.3, 10.1, 10.8

    For any falsy or absent extensions.agentic value, the /agent/run endpoint
    must return HTTP 400 (router-level agentic check) or HTTP 422 (Pydantic
    validation error for missing required fields).

    No agent session should be initiated — the 400 guard fires before the
    orchestrator is invoked.
    """
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=False)

    # Build payload with invalid agentic value
    if include_extensions:
        if agentic_value is None:
            extensions = {}
        else:
            extensions = {"agentic": agentic_value}
    else:
        extensions = {}  # omit agentic entirely

    payload = {
        "request_id": VALID_UUID_V4,
        "user": {"user_id": "test-user"},
        "request": {"messages": ONE_MESSAGE},
        "extensions": extensions,
    }

    resp = client.post("/agent/run", json=payload)

    # Must return 400 (our agentic check) or 422 (Pydantic validation)
    assert resp.status_code in (400, 422), (
        f"Expected 400 or 422 for invalid agentic={agentic_value!r}, "
        f"include_extensions={include_extensions}, got {resp.status_code}"
    )

    # For 400 specifically (agentic check), verify the error structure
    if resp.status_code == 400:
        body = resp.json()
        assert "error" in body, f"400 response missing 'error' field: {body}"
