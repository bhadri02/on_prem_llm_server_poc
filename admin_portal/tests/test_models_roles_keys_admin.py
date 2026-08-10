"""
admin_portal/tests/test_models_roles_keys_admin.py

Tests for the admin-console backend surface added for the static mockup
integration (Phase 5):
  - POST /portal/models              (register — proxies to Model Registry)
  - PATCH /portal/models/{name}/api-key (proxies to Model Registry)
  - GET  /portal/keys/               (admin-wide key listing, DB-backed)
  - PATCH /portal/roles/{role}/permissions (DB-backed, editable matrix)

Model Registry proxy endpoints are tested with respx (mocking the outbound
httpx call); the DB-backed endpoints reuse the isolated temp-SQLite
dependency-override pattern from test_users_and_keys_api.py.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def set_required_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")


@pytest.fixture
def app(tmp_path, set_required_env):
    from admin_portal import config as _cfg

    _cfg.settings.GATEWAY_API_KEY = "test-key"
    _cfg.settings.MODEL_REGISTRY_URL = "http://model-registry-mock:5001"
    _cfg.settings.REGISTRY_API_KEY = "test-registry-key"

    from admin_portal.db.models import Base, User
    from admin_portal.db.seed import run_startup_seed
    from admin_portal.db.session import get_db
    from admin_portal.main import app as _app
    from admin_portal.services.session_auth import AuthContext, get_current_session

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(engine)
    seed_db = test_session_local()
    try:
        run_startup_seed(seed_db, "test-key")
    finally:
        seed_db.close()

    def _override_get_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    def _override_get_current_session() -> AuthContext:
        # These tests predate login (Phase 6) and exercise admin-only CRUD
        # directly — stand in as an already-authenticated admin.
        return AuthContext(
            user=User(user_id="test-admin", username="test-admin", status="active"),
            roles=["admin"],
            api_key_raw="test-key",
        )

    _app.dependency_overrides[get_db] = _override_get_db
    _app.dependency_overrides[get_current_session] = _override_get_current_session
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /portal/models — register (proxy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_register_model_proxies_to_registry(client):
    route = respx.post("http://model-registry-mock:5001/models/").mock(
        return_value=httpx.Response(
            201,
            json={
                "name": "claude-sonnet-5",
                "version": "1.0",
                "backend": "anthropic",
                "endpoint": "https://api.anthropic.com",
                "tasks": ["chat"],
                "status": "active",
                "vram_required_gb": None,
                "max_context_length": None,
                "fallback_model": None,
                "registered_at": "2026-01-01T00:00:00Z",
                "notes": None,
                "api_key_set": True,
            },
        )
    )

    r = await client.post(
        "/portal/models",
        json={
            "name": "claude-sonnet-5",
            "version": "1.0",
            "backend": "anthropic",
            "endpoint": "https://api.anthropic.com",
            "tasks": ["chat"],
            "status": "active",
            "api_key": "sk-ant-secret",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["api_key_set"] is True
    assert "api_key" not in body

    # The raw key must have been forwarded upstream, never dropped.
    sent_body = route.calls[0].request.content
    assert b"sk-ant-secret" in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_register_model_upstream_unavailable_returns_502(client):
    respx.post("http://model-registry-mock:5001/models/").mock(side_effect=httpx.ConnectError("boom"))
    r = await client.post(
        "/portal/models",
        json={
            "name": "m1", "version": "1.0", "backend": "ollama",
            "endpoint": "http://x", "tasks": ["chat"], "status": "active",
        },
    )
    assert r.status_code == 502
    assert r.json()["upstream"] == "model-registry"


# ---------------------------------------------------------------------------
# PATCH /portal/models/{name}/api-key (proxy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_patch_model_api_key_proxies(client):
    respx.patch("http://model-registry-mock:5001/models/claude-sonnet-5/api-key").mock(
        return_value=httpx.Response(200, json={"name": "claude-sonnet-5", "api_key_set": True})
    )
    r = await client.patch("/portal/models/claude-sonnet-5/api-key", json={"api_key": "sk-ant-new"})
    assert r.status_code == 200
    assert r.json()["api_key_set"] is True


@pytest.mark.asyncio
@respx.mock
async def test_patch_model_api_key_404_maps_to_not_found(client):
    respx.patch("http://model-registry-mock:5001/models/does-not-exist/api-key").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    r = await client.patch("/portal/models/does-not-exist/api-key", json={"api_key": "x"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


# ---------------------------------------------------------------------------
# GET /portal/keys/ — admin-wide listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_all_keys_includes_seeded_legacy_key(client):
    r = await client.get("/portal/keys/")
    assert r.status_code == 200
    keys = r.json()
    assert len(keys) == 1
    assert keys[0]["owner_username"] == "admin"
    assert keys[0]["label"] == "Legacy POC key"
    assert "key_hash" not in keys[0]


@pytest.mark.asyncio
async def test_list_all_keys_spans_multiple_users(client):
    r = await client.post("/portal/users/", json={"username": "keyowner", "roles": ["developer"]})
    user_id = r.json()["user_id"]
    await client.post(f"/portal/users/{user_id}/keys", json={"label": "second user key"})

    r = await client.get("/portal/keys/")
    assert r.status_code == 200
    owners = {k["owner_username"] for k in r.json()}
    assert owners == {"admin", "keyowner"}


# ---------------------------------------------------------------------------
# PATCH /portal/roles/{role}/permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_role_permissions_upserts_and_persists(client):
    r = await client.get("/portal/roles/analyst/permissions")
    assert r.json()["permissions"].get("code") is not True  # not seeded — sparse "absence = deny"

    r = await client.patch("/portal/roles/analyst/permissions", json={"permissions": {"code": True}})
    assert r.status_code == 200
    assert r.json()["permissions"]["code"] is True

    # Persisted — a fresh GET reflects it, and pre-existing rows are untouched.
    r = await client.get("/portal/roles/analyst/permissions")
    perms = r.json()["permissions"]
    assert perms["code"] is True
    assert perms["chat"] is True  # unrelated seeded row still present


@pytest.mark.asyncio
async def test_patch_role_permissions_can_revoke_existing_grant(client):
    r = await client.patch("/portal/roles/developer/permissions", json={"permissions": {"code": False}})
    assert r.status_code == 200
    assert r.json()["permissions"]["code"] is False


@pytest.mark.asyncio
async def test_patch_role_permissions_unknown_role_404(client):
    r = await client.patch("/portal/roles/not-a-role/permissions", json={"permissions": {"chat": True}})
    assert r.status_code == 404
