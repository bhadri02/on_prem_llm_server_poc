"""
tests/smoke/test_health.py — HTTP smoke tests for the API Gateway.

Covers:
  9.2.1 — GET /health without X-Api-Key returns 200 {"status": "ok"}
  9.2.2 — GET /v1/chat/completions returns 405 Method Not Allowed
  9.2.3 — GET /undefined/path/that/does/not/exist returns 404
  9.2.4 — 61 sequential requests with the same API key; 61st returns 429
           with Retry-After: 60 and the canonical error body

``api_gateway.main`` calls ``get_settings()`` at module import time and
calls ``sys.exit(1)`` if ``GATEWAY_API_KEY`` is missing.  To avoid this
during pytest collection we defer the ``create_app`` import to inside each
fixture (after monkeypatch has set the required env vars).

Validates: Requirements 1.3, 1.4, 1.7, 3.4
"""

from __future__ import annotations

import pytest

VALID_KEY = "test-key"
DOWNSTREAM_URL = "http://security-layer:8081"


# ---------------------------------------------------------------------------
# Shared fixture: set env vars, clear caches, create app, yield TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def gateway_client(monkeypatch):
    """TestClient backed by a freshly created API Gateway app.

    Env vars are set via monkeypatch *before* any api_gateway module is
    imported, so the module-level get_settings() call in main.py succeeds.
    """
    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)

    # Import after env vars are in place to avoid the sys.exit(1) guard.
    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from starlette.testclient import TestClient

    get_settings.cache_clear()

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 9.2.1 — GET /health without X-Api-Key returns 200 {"status": "ok"}
# ---------------------------------------------------------------------------


def test_health_no_auth_key_returns_200(gateway_client):
    """/health is auth-exempt; GET without X-Api-Key must return 200 {"status": "ok"}.

    Validates: Requirements 1.3
    """
    response = gateway_client.get("/health")

    assert response.status_code == 200, (
        f"Expected 200 from /health, got {response.status_code}: {response.text}"
    )
    assert response.json() == {"status": "ok"}, (
        f"Expected body {{'status': 'ok'}}, got {response.json()!r}"
    )


# ---------------------------------------------------------------------------
# 9.2.2 — GET /v1/chat/completions returns 405 Method Not Allowed
# ---------------------------------------------------------------------------


def test_get_chat_completions_returns_405(gateway_client):
    """GET /v1/chat/completions (wrong method) must return 405 Method Not Allowed.

    The route only accepts POST; FastAPI returns 405 for any other method.

    Validates: Requirements 1.4
    """
    response = gateway_client.get(
        "/v1/chat/completions",
        headers={"X-Api-Key": VALID_KEY},
    )

    assert response.status_code == 405, (
        f"Expected 405 for GET /v1/chat/completions, "
        f"got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# 9.2.3 — Undefined path returns 404
# ---------------------------------------------------------------------------


def test_undefined_path_returns_404(gateway_client):
    """GET on a path not registered with the app must return 404.

    The X-Api-Key is included so that AuthMiddleware passes the request
    through to the router; the router then returns 404 because the path
    is not registered.

    Validates: Requirements 1.7
    """
    response = gateway_client.get(
        "/undefined/path/that/does/not/exist",
        headers={"X-Api-Key": VALID_KEY},
    )

    assert response.status_code == 404, (
        f"Expected 404 for unknown path, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# 9.2.4 — 61 sequential requests with the same key; 61st returns 429
# ---------------------------------------------------------------------------


def test_rate_limit_61st_request_returns_429(monkeypatch):
    """61 sequential requests with the same API key; 61st must return 429.

    Strategy:
      - Use GET /v1/models (requires auth, no downstream call needed) so all
        60 allowed requests complete as 200 without a real security service.
      - The 61st request must be rejected with HTTP 429, Retry-After: 60,
        and the canonical error body.
      - RateLimitMiddleware._store is cleared before the test to ensure no
        residual state from other tests bleeds in.

    Validates: Requirements 3.4
    """
    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)

    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from api_gateway.middleware.rate_limit import RateLimitMiddleware
    from starlette.testclient import TestClient

    get_settings.cache_clear()

    # Clear any leftover timestamps from other tests.
    RateLimitMiddleware._store.clear()

    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        # First 60 requests must all be 200.
        for i in range(1, 61):
            resp = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})
            assert resp.status_code == 200, (
                f"Request #{i}/60 expected 200, got {resp.status_code}: {resp.text}"
            )

        # 61st request must be rate-limited.
        resp_61 = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})

    assert resp_61.status_code == 429, (
        f"Expected 429 on request #61, got {resp_61.status_code}: {resp_61.text}"
    )

    retry_after = resp_61.headers.get("Retry-After", "")
    assert retry_after == "60", (
        f"Expected Retry-After header == '60', got {retry_after!r}"
    )

    body = resp_61.json()
    assert body == {"error": {"code": "429", "message": "Rate limit exceeded"}}, (
        f"Unexpected 429 body: {body!r}"
    )

    get_settings.cache_clear()
