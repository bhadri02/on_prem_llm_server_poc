"""
admin_portal/services/session_auth.py

Session-based auth dependencies for /portal/* routes (Phase 6). Every
protected route depends on `get_current_session` (any logged-in user) or
`require_admin` (must additionally hold the "admin" role) — resolved from
the httpOnly session cookie, never a header the browser has to manage.

Exempt by design: /portal/health, /portal/config, /portal/auth/login, and
/portal/keys/resolve (service-to-service, guarded by ADMIN_PORTAL_INTERNAL_KEY
instead — see routers/keys.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from admin_portal.config import settings
from admin_portal.db.models import Session as SessionModel
from admin_portal.db.models import User, UserRole
from admin_portal.db.session import get_db

_UNAUTHORIZED = {"error": "unauthorized", "message": "Not logged in."}


@dataclass
class AuthContext:
    user: User
    roles: list[str]
    api_key_raw: str  # session-scoped key — used by chat.py to proxy on this user's behalf


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < datetime.now(timezone.utc)


def get_current_session(request: Request, db: DBSession = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    sess = db.get(SessionModel, token)
    if sess is None:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    if _is_expired(sess.expires_at):
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail={"error": "session_expired", "message": "Session expired — please log in again."})

    user = db.get(User, sess.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    roles = [
        role_name
        for (role_name,) in db.execute(
            select(UserRole.role_name).where(UserRole.user_id == user.user_id)
        ).all()
    ]
    return AuthContext(user=user, roles=roles, api_key_raw=sess.api_key_raw)


def require_admin(ctx: AuthContext = Depends(get_current_session)) -> AuthContext:
    if "admin" not in ctx.roles:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "admin role required."})
    return ctx
