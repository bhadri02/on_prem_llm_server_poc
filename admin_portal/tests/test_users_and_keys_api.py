"""
admin_portal/tests/test_users_and_keys_api.py

Phase 1 (key resolve) + Phase 3 (user/role/API-key management) endpoint
tests. Uses an isolated temp-file SQLite DB via FastAPI's
``dependency_overrides`` on ``get_db`` so tests never touch a real Postgres.
"""

from __future__ import annotations

import pytest
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
    _cfg.settings.ADMIN_PORTAL_INTERNAL_KEY = "test-internal-key"

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
# GET /portal/keys/resolve (Phase 1)
# ---------------------------------------------------------------------------


class TestKeyResolve:
    @pytest.mark.asyncio
    async def test_resolve_requires_internal_key(self, client):
        r = await client.get("/portal/keys/resolve", params={"key": "test-key"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_resolve_wrong_internal_key_rejected(self, client):
        r = await client.get(
            "/portal/keys/resolve",
            params={"key": "test-key"},
            headers={"X-Portal-Internal-Key": "wrong"},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_resolve_legacy_key_returns_admin_profile(self, client):
        r = await client.get(
            "/portal/keys/resolve",
            params={"key": "test-key"},
            headers={"X-Portal-Internal-Key": "test-internal-key"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["roles"] == ["admin"]
        assert body["model_entitlements"] == []

    @pytest.mark.asyncio
    async def test_resolve_unknown_key_returns_404(self, client):
        r = await client.get(
            "/portal/keys/resolve",
            params={"key": "no-such-key"},
            headers={"X-Portal-Internal-Key": "test-internal-key"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /portal/policy/matrix (Phase 6 — dynamic policy enforcement)
# ---------------------------------------------------------------------------


class TestPolicyMatrix:
    """Internal endpoint polled by intelligent_router (services/policy_resolver.py)
    so PATCH /portal/roles/{role}/permissions takes effect on real request
    routing without a policy_matrix.yaml edit + Router restart."""

    @pytest.mark.asyncio
    async def test_requires_internal_key(self, client):
        r = await client.get("/portal/policy/matrix")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_internal_key_rejected(self, client):
        r = await client.get("/portal/policy/matrix", headers={"X-Portal-Internal-Key": "wrong"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_full_seeded_matrix(self, client):
        r = await client.get(
            "/portal/policy/matrix", headers={"X-Portal-Internal-Key": "test-internal-key"}
        )
        assert r.status_code == 200
        body = r.json()

        assert body["admin"] == {
            "chat": True, "code": True, "reasoning": True, "summarization": True, "translation": True,
        }
        assert body["analyst"] == {"chat": True, "summarization": True, "translation": True}
        # viewer has zero seeded rows — absent entirely, not present with False values
        assert "viewer" not in body

    @pytest.mark.asyncio
    async def test_reflects_admin_edits_immediately(self, client):
        """The whole point: a PATCH to role_permissions must show up here on
        the very next call — no caching on the admin_portal side (the
        caching lives entirely on intelligent_router's side)."""
        before = await client.get(
            "/portal/policy/matrix", headers={"X-Portal-Internal-Key": "test-internal-key"}
        )
        assert before.json().get("viewer", {}).get("chat") is not True

        patch = await client.patch("/portal/roles/viewer/permissions", json={"permissions": {"chat": True}})
        assert patch.status_code == 200

        after = await client.get(
            "/portal/policy/matrix", headers={"X-Portal-Internal-Key": "test-internal-key"}
        )
        assert after.json()["viewer"]["chat"] is True


# ---------------------------------------------------------------------------
# /portal/users/* CRUD (Phase 3)
# ---------------------------------------------------------------------------


class TestUserCrud:
    @pytest.mark.asyncio
    async def test_create_list_get_user(self, client):
        r = await client.post("/portal/users/", json={"username": "alice", "roles": ["developer"]})
        assert r.status_code == 201
        user = r.json()
        assert user["roles"] == ["developer"]
        assert user["status"] == "active"

        r = await client.get("/portal/users/")
        assert r.status_code == 200
        assert any(u["username"] == "alice" for u in r.json())

        r = await client.get(f"/portal/users/{user['user_id']}")
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    @pytest.mark.asyncio
    async def test_create_duplicate_username_conflicts(self, client):
        await client.post("/portal/users/", json={"username": "bob", "roles": []})
        r = await client.post("/portal/users/", json={"username": "bob", "roles": []})
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_create_with_unknown_role_rejected(self, client):
        r = await client.post("/portal/users/", json={"username": "carol", "roles": ["not-a-role"]})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_roles_replaces_assignment(self, client):
        r = await client.post("/portal/users/", json={"username": "dave", "roles": ["viewer"]})
        user_id = r.json()["user_id"]

        r = await client.patch(f"/portal/users/{user_id}/roles", json={"roles": ["analyst"]})
        assert r.status_code == 200
        assert r.json()["roles"] == ["analyst"]

    @pytest.mark.asyncio
    async def test_deactivate_user(self, client):
        r = await client.post("/portal/users/", json={"username": "erin", "roles": ["viewer"]})
        user_id = r.json()["user_id"]

        r = await client.delete(f"/portal/users/{user_id}")
        assert r.status_code == 204

        r = await client.get(f"/portal/users/{user_id}")
        assert r.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_get_unknown_user_404(self, client):
        r = await client.get("/portal/users/00000000-0000-0000-0000-000000000099")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /portal/users/{id}/keys/* lifecycle (Phase 3)
# ---------------------------------------------------------------------------


class TestApiKeyLifecycle:
    @pytest.mark.asyncio
    async def test_generate_list_revoke_key(self, client):
        r = await client.post("/portal/users/", json={"username": "frank", "roles": ["developer"]})
        user_id = r.json()["user_id"]

        r = await client.post(
            f"/portal/users/{user_id}/keys",
            json={"label": "laptop", "model_entitlements": ["llama3.2:3b"]},
        )
        assert r.status_code == 201
        created = r.json()
        assert "raw_key" in created and len(created["raw_key"]) > 10
        assert created["model_entitlements"] == ["llama3.2:3b"]
        key_id = created["key_id"]

        r = await client.get(f"/portal/users/{user_id}/keys")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert "raw_key" not in r.json()[0]

        r = await client.patch(
            f"/portal/users/{user_id}/keys/{key_id}/models",
            json={"model_entitlements": []},
        )
        assert r.status_code == 200
        assert r.json()["model_entitlements"] == []

        r = await client.delete(f"/portal/users/{user_id}/keys/{key_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_created_key_resolves_via_internal_endpoint(self, client):
        r = await client.post("/portal/users/", json={"username": "grace", "roles": ["analyst"]})
        user_id = r.json()["user_id"]

        r = await client.post(f"/portal/users/{user_id}/keys", json={})
        raw_key = r.json()["raw_key"]

        r = await client.get(
            "/portal/keys/resolve",
            params={"key": raw_key},
            headers={"X-Portal-Internal-Key": "test-internal-key"},
        )
        assert r.status_code == 200
        assert r.json()["roles"] == ["analyst"]

    @pytest.mark.asyncio
    async def test_key_created_without_rate_limit_gets_concrete_default(self, client):
        """rate_limit_rpm is no longer nullable — a key created without one
        specified must still get a real, concrete value (not None), and
        that value must be what /keys/resolve hands the API Gateway too."""
        from admin_portal.db.models import DEFAULT_RATE_LIMIT_RPM

        r = await client.post("/portal/users/", json={"username": "henry", "roles": ["developer"]})
        user_id = r.json()["user_id"]

        r = await client.post(f"/portal/users/{user_id}/keys", json={"label": "no-limit-specified"})
        assert r.status_code == 201
        created = r.json()
        assert created["rate_limit_rpm"] == DEFAULT_RATE_LIMIT_RPM

        r = await client.get(
            "/portal/keys/resolve",
            params={"key": created["raw_key"]},
            headers={"X-Portal-Internal-Key": "test-internal-key"},
        )
        assert r.status_code == 200
        assert r.json()["rate_limit_override"] == DEFAULT_RATE_LIMIT_RPM

    @pytest.mark.asyncio
    async def test_patch_key_rate_limit_changes_the_effective_limit(self, client):
        r = await client.post("/portal/users/", json={"username": "iris", "roles": ["developer"]})
        user_id = r.json()["user_id"]

        r = await client.post(f"/portal/users/{user_id}/keys", json={"label": "agentic-workload"})
        key_id = r.json()["key_id"]
        raw_key = r.json()["raw_key"]

        r = await client.patch(
            f"/portal/users/{user_id}/keys/{key_id}/rate-limit",
            json={"rate_limit_rpm": 500},
        )
        assert r.status_code == 200
        assert r.json()["rate_limit_rpm"] == 500

        r = await client.get(
            "/portal/keys/resolve",
            params={"key": raw_key},
            headers={"X-Portal-Internal-Key": "test-internal-key"},
        )
        assert r.json()["rate_limit_override"] == 500

    @pytest.mark.asyncio
    async def test_patch_key_rate_limit_rejects_non_positive_value(self, client):
        r = await client.post("/portal/users/", json={"username": "jack", "roles": ["developer"]})
        user_id = r.json()["user_id"]
        r = await client.post(f"/portal/users/{user_id}/keys", json={})
        key_id = r.json()["key_id"]

        r = await client.patch(
            f"/portal/users/{user_id}/keys/{key_id}/rate-limit",
            json={"rate_limit_rpm": 0},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /portal/roles/* (Phase 3, read-only)
# ---------------------------------------------------------------------------


class TestRoles:
    @pytest.mark.asyncio
    async def test_list_roles(self, client):
        r = await client.get("/portal/roles/")
        assert r.status_code == 200
        names = {role["role_name"] for role in r.json()}
        assert names == {"viewer", "analyst", "developer", "admin"}

    @pytest.mark.asyncio
    async def test_role_permissions(self, client):
        r = await client.get("/portal/roles/analyst/permissions")
        assert r.status_code == 200
        perms = r.json()["permissions"]
        assert perms["chat"] is True
        assert perms.get("code") is not True

    @pytest.mark.asyncio
    async def test_unknown_role_404(self, client):
        r = await client.get("/portal/roles/not-a-role/permissions")
        assert r.status_code == 404
