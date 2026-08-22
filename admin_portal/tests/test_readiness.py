"""
admin_portal/tests/test_readiness.py

Tests for GET /portal/ready (admin_portal/routers/health.py) — the
DB-connectivity readiness probe added alongside the existing pure-liveness
/portal/health. Uses the same isolated temp-file SQLite + get_db override
pattern as test_auth.py / test_users_and_keys_api.py so it never touches a
real Postgres.
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
    from admin_portal.db.models import Base
    from admin_portal.db.session import get_db
    from admin_portal.main import app as _app
    from admin_portal.routers import health as health_module

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def _override_get_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    _app.dependency_overrides[get_db] = _override_get_db
    yield _app
    _app.dependency_overrides.clear()
    health_module.clear_startup_failure()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestReadinessHealthy:
    async def test_returns_200_when_db_reachable(self, client):
        response = await client.get("/portal/ready")
        assert response.status_code == 200

    async def test_body_status_ready(self, client):
        response = await client.get("/portal/ready")
        assert response.json()["status"] == "ready"


class TestReadinessDbUnreachable:
    @pytest.fixture
    def app(self, tmp_path, set_required_env):
        """Override get_db with a session whose execute() always raises,
        simulating an unreachable Postgres without needing a real one."""
        from admin_portal.db.session import get_db
        from admin_portal.main import app as _app
        from admin_portal.routers import health as health_module

        class _BrokenSession:
            def execute(self, *args, **kwargs):
                raise ConnectionRefusedError("could not connect to server")

            def close(self):
                pass

        def _override_get_db():
            db = _BrokenSession()
            try:
                yield db
            finally:
                db.close()

        _app.dependency_overrides[get_db] = _override_get_db
        yield _app
        _app.dependency_overrides.clear()
        health_module.clear_startup_failure()

    async def test_returns_503_when_db_unreachable(self, client):
        response = await client.get("/portal/ready")
        assert response.status_code == 503

    async def test_body_status_not_ready(self, client):
        response = await client.get("/portal/ready")
        body = response.json()
        assert body["status"] == "not_ready"
        assert "unreachable" in body["reason"]


class TestReadinessStartupFailure:
    async def test_startup_failure_takes_precedence_over_db_check(self, client):
        from admin_portal.routers import health as health_module

        health_module.set_startup_failure("test: dependency missing")
        try:
            response = await client.get("/portal/ready")
            assert response.status_code == 503
            assert response.json()["status"] == "degraded"
        finally:
            health_module.clear_startup_failure()


class TestHealthUnaffectedByReadiness:
    """/portal/health must remain a pure liveness check — no DB access."""

    @pytest.fixture
    def app(self, tmp_path, set_required_env):
        """Reuse the broken-DB override from TestReadinessDbUnreachable to
        prove /health's 200 doesn't depend on the database being reachable."""
        from admin_portal.db.session import get_db
        from admin_portal.main import app as _app
        from admin_portal.routers import health as health_module

        class _BrokenSession:
            def execute(self, *args, **kwargs):
                raise ConnectionRefusedError("could not connect to server")

            def close(self):
                pass

        def _override_get_db():
            db = _BrokenSession()
            try:
                yield db
            finally:
                db.close()

        _app.dependency_overrides[get_db] = _override_get_db
        yield _app
        _app.dependency_overrides.clear()
        health_module.clear_startup_failure()

    async def test_health_still_returns_200_with_db_unreachable(self, client):
        response = await client.get("/portal/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
