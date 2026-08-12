"""
tests/smoke/test_health.py — HTTP smoke tests for the API Gateway.

Covers:
  9.2.1 — GET /health without X-Api-Key returns 200 {"status": "ok"}
  9.2.2 — GET /v1/chat/completions returns 405 Method Not Allowed
  9.2.3 — GET /undefined/path/that/does/not/exist returns 404
  9.2.4 — Per-key rate limiting (Redis-backed, fakeredis in tests): a key's
           own limit blocks further requests, keys are isolated from each
           other, and Redis unavailability fails open

``api_gateway.main`` calls ``get_settings()`` at module import time and
calls ``sys.exit(1)`` if ``GATEWAY_API_KEY`` is missing.  To avoid this
during pytest collection we defer the ``create_app`` import to inside each
fixture (after monkeypatch has set the required env vars).

Validates: Requirements 1.3, 1.4, 1.7, 3.4
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

VALID_KEY = "test-key"
DOWNSTREAM_URL = "http://security-layer:8081"


# ---------------------------------------------------------------------------
# Identity resolution stub (Phase 2 — RBAC + per-user API keys)
#
# AuthMiddleware now resolves X-Api-Key against the Admin Portal instead of
# comparing to a static secret. These smoke tests aren't exercising identity
# resolution itself, so patch it to mirror the old static-key behaviour:
# VALID_KEY resolves to a normal developer identity, anything else is unknown.
# ---------------------------------------------------------------------------

async def _fake_resolve_key(key, client):
    from api_gateway.services.key_resolver import KeyProfile

    if key == VALID_KEY:
        return KeyProfile(
            user_id="poc-user",
            username="poc-user",
            department="poc",
            roles=["developer"],
            model_entitlements=[],
            key_id="test-key-id",
            rate_limit_override=60,
        )
    return None


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
    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", _fake_resolve_key)

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
# Rate limiting — per-key only, Redis-backed (fakeredis in tests).
#
# There is no platform-wide request-count fallback: every resolved key
# carries its own concrete rate_limit_override, and RateLimitMiddleware
# reads only that value. Real Redis is swapped for fakeredis.aioredis so
# these tests are deterministic and don't depend on a running Redis.
# ---------------------------------------------------------------------------


@contextmanager
def _rate_limit_client(monkeypatch, resolve_key_fn):
    """Yield a TestClient with a fresh fakeredis backing RateLimitMiddleware.

    The app's lifespan creates a real redis.asyncio client pointed at
    whatever REDIS_URL resolves to; entering the TestClient context runs
    that lifespan, then this swaps app.state.redis for an isolated
    fakeredis instance before any request is made.
    """
    import fakeredis.aioredis

    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)

    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from starlette.testclient import TestClient

    get_settings.cache_clear()
    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", resolve_key_fn)

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
        yield client
    get_settings.cache_clear()


def test_rate_limit_blocks_once_key_specific_limit_is_reached(monkeypatch):
    """A key with rate_limit_override=5 gets exactly 5 successful requests;
    the 6th must return 429 with Retry-After and the canonical error body.

    Validates: Requirements 3.4
    """
    limit = 5

    async def _fake_resolve(key, client):
        from api_gateway.services.key_resolver import KeyProfile

        if key == VALID_KEY:
            return KeyProfile(
                user_id="poc-user",
                username="poc-user",
                department="poc",
                roles=["developer"],
                model_entitlements=[],
                key_id="test-key-id",
                rate_limit_override=limit,
            )
        return None

    with _rate_limit_client(monkeypatch, _fake_resolve) as client:
        for i in range(1, limit + 1):
            resp = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})
            assert resp.status_code == 200, (
                f"Request #{i}/{limit} expected 200, got {resp.status_code}: {resp.text}"
            )

        resp_over = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})

    assert resp_over.status_code == 429, (
        f"Expected 429 once the {limit}-request limit is exceeded, "
        f"got {resp_over.status_code}: {resp_over.text}"
    )
    assert resp_over.headers.get("Retry-After") == "60"
    assert resp_over.json() == {"error": {"code": "429", "message": "Rate limit exceeded"}}


def test_rate_limit_is_isolated_per_key(monkeypatch):
    """Two keys with different limits must not affect each other — one
    key hitting its own limit must not block the other key's requests.

    Validates: rate limiting is per-API-key, not global or per-user-wide.
    """
    key_a, limit_a = "key-a", 2
    key_b, limit_b = "key-b", 10

    async def _fake_resolve(key, client):
        from api_gateway.services.key_resolver import KeyProfile

        limits = {key_a: limit_a, key_b: limit_b}
        if key not in limits:
            return None
        return KeyProfile(
            user_id=f"user-{key}",
            username=f"user-{key}",
            department="poc",
            roles=["developer"],
            model_entitlements=[],
            key_id=f"{key}-id",
            rate_limit_override=limits[key],
        )

    with _rate_limit_client(monkeypatch, _fake_resolve) as client:
        # Exhaust key_a's limit (2 requests).
        for _ in range(limit_a):
            resp = client.get("/v1/models", headers={"X-Api-Key": key_a})
            assert resp.status_code == 200
        resp_a_over = client.get("/v1/models", headers={"X-Api-Key": key_a})
        assert resp_a_over.status_code == 429

        # key_b must be completely unaffected by key_a's exhausted limit.
        for i in range(1, limit_b + 1):
            resp = client.get("/v1/models", headers={"X-Api-Key": key_b})
            assert resp.status_code == 200, (
                f"key_b request #{i}/{limit_b} expected 200, got {resp.status_code}: {resp.text}"
            )


def test_rate_limit_fails_open_when_redis_unavailable(monkeypatch):
    """If Redis itself is unreachable, requests must still succeed (fail
    open) rather than the Gateway going down or every request 500ing —
    rate limiting is a cost/abuse control, not an auth boundary."""
    monkeypatch.setenv("GATEWAY_API_KEY", VALID_KEY)
    monkeypatch.setenv("DOWNSTREAM_SECURITY_URL", DOWNSTREAM_URL)
    # Point at a port nothing is listening on so every Redis call fails fast.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")

    from api_gateway.config import get_settings
    from api_gateway.main import create_app
    from starlette.testclient import TestClient

    get_settings.cache_clear()
    monkeypatch.setattr("api_gateway.middleware.auth.resolve_key", _fake_resolve_key)

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/v1/models", headers={"X-Api-Key": VALID_KEY})

    assert resp.status_code == 200, (
        f"Expected 200 (fail open) when Redis is unreachable, "
        f"got {resp.status_code}: {resp.text}"
    )

    get_settings.cache_clear()
