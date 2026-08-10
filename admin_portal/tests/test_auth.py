"""
admin_portal/tests/test_auth.py

Phase 6 — real password login + session tests. Uses an isolated temp-file
SQLite DB (same pattern as test_users_and_keys_api.py) so tests never touch
a real Postgres. Unlike the other admin_portal test files, this one does
NOT override get_current_session — it exercises the real login flow.
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
    _cfg.settings.SEED_ADMIN_PASSWORD = "admin123"

    from admin_portal.db.models import Base
    from admin_portal.db.seed import run_startup_seed
    from admin_portal.db.session import get_db
    from admin_portal.main import app as _app

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(engine)
    seed_db = test_session_local()
    try:
        run_startup_seed(seed_db, "test-key", "admin123")
    finally:
        seed_db.close()

    def _override_get_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    _app.dependency_overrides[get_db] = _override_get_db
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_with_seeded_admin_succeeds(client):
    r = await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert "admin" in body["roles"]
    assert "portal_session" in r.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_401(client):
    r = await client.post("/portal/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_unknown_username_401(client):
    r = await client.post("/portal/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Session gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_route_without_login_401(client):
    r = await client.get("/portal/users/")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_route_with_login_succeeds(client):
    login = await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200
    r = await client.get("/portal/users/")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_user_gets_403_on_admin_route(client):
    # Create a developer (non-admin) user as the seeded admin, then log in as them.
    login = await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200

    created = await client.post(
        "/portal/users/", json={"username": "dev1", "roles": ["developer"], "password": "devpass123"}
    )
    assert created.status_code == 201

    await client.post("/portal/auth/logout")

    dev_login = await client.post("/portal/auth/login", json={"username": "dev1", "password": "devpass123"})
    assert dev_login.status_code == 200
    assert dev_login.json()["roles"] == ["developer"]

    r = await client.get("/portal/users/")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_me_reflects_logged_in_identity(client):
    await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    r = await client.get("/portal/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_me_without_login_401(client):
    r = await client.get("/portal/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_invalidates_session(client):
    await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    assert (await client.get("/portal/auth/me")).status_code == 200

    logout = await client.post("/portal/auth/logout")
    assert logout.status_code == 204

    assert (await client.get("/portal/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_password_reset_allows_new_login(client):
    await client.post("/portal/auth/login", json={"username": "admin", "password": "admin123"})
    created = await client.post("/portal/users/", json={"username": "dev2", "roles": ["viewer"]})
    user_id = created.json()["user_id"]

    # No password set yet — can't log in.
    fail = await client.post("/portal/auth/login", json={"username": "dev2", "password": "anything"})
    assert fail.status_code == 401

    reset = await client.patch(f"/portal/users/{user_id}/password", json={"password": "newpass123"})
    assert reset.status_code == 200

    await client.post("/portal/auth/logout")
    ok = await client.post("/portal/auth/login", json={"username": "dev2", "password": "newpass123"})
    assert ok.status_code == 200
