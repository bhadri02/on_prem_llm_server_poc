"""
admin_portal/routers/auth.py

Real password login for the Admin Portal (Phase 6) — both regular users and
admins authenticate here. On success, a session cookie is set (httpOnly —
the browser never sees or manages an API key). Under the hood, login mints
a real ApiKey row (hashed, like any other key) so the existing
api_gateway <-> admin_portal /portal/keys/resolve contract needs no changes;
a session-derived chat request resolves through the exact same path a
manually-created key would. See services/session_auth.py for the session
dependency used to gate every other /portal/* router.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from admin_portal.config import settings
from admin_portal.db.keys import generate_raw_key, hash_key, key_prefix
from admin_portal.db.models import ApiKey, KeyModelEntitlement
from admin_portal.db.models import Session as SessionModel
from admin_portal.db.models import User, UserRole
from admin_portal.db.passwords import verify_password
from admin_portal.db.session import get_db
from admin_portal.schemas.auth import LoginRequest, MeResponse
from admin_portal.services.session_auth import AuthContext, get_current_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_roles(db: DBSession, user_id: str) -> list[str]:
    return [
        role_name
        for (role_name,) in db.execute(select(UserRole.role_name).where(UserRole.user_id == user_id)).all()
    ]


@router.post("/login", response_model=MeResponse)
async def login(body: LoginRequest, response: Response, db: DBSession = Depends(get_db)) -> MeResponse:
    user = db.query(User).filter_by(username=body.username).first()
    if user is None or user.status != "active" or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Invalid username or password."},
        )

    # Mint a real, session-scoped API key — resolves through the unchanged
    # /portal/keys/resolve path exactly like any manually-created key.
    #
    # Its model_entitlements must inherit from the user's OTHER active keys,
    # not default to "all models" — otherwise logging in would silently
    # bypass whatever entitlement restriction an admin set on that user's
    # designated key. If the user holds ANY unrestricted (empty-entitlement)
    # active key already, or holds no keys at all yet, the session key stays
    # unrestricted too; otherwise it gets the union of what their other
    # active keys are entitled to.
    other_keys = db.query(ApiKey).filter_by(user_id=user.user_id, status="active").all()
    session_entitlements: list[str] = []
    if other_keys:
        entitlement_union: set[str] = set()
        unrestricted = False
        for k in other_keys:
            names = [e.model_name for e in k.model_entitlements]
            if not names:
                unrestricted = True
                break
            entitlement_union.update(names)
        if not unrestricted:
            session_entitlements = sorted(entitlement_union)

    raw_key = generate_raw_key()
    api_key = ApiKey(
        user_id=user.user_id,
        key_hash=hash_key(raw_key),
        key_prefix=key_prefix(raw_key),
        label="Login session key",
    )
    db.add(api_key)
    db.flush()
    for model_name in session_entitlements:
        db.add(KeyModelEntitlement(key_id=api_key.key_id, model_name=model_name))

    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)
    db.add(
        SessionModel(
            session_id=session_id,
            user_id=user.user_id,
            api_key_id=api_key.key_id,
            api_key_raw=raw_key,
            expires_at=expires_at,
        )
    )
    db.commit()

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_TTL_HOURS * 3600,
    )

    return MeResponse(
        user_id=user.user_id,
        username=user.username,
        department=user.department,
        roles=_user_roles(db, user.user_id),
    )


@router.post("/logout", status_code=204, response_model=None)
async def logout(request: Request, response: Response, db: DBSession = Depends(get_db)) -> None:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token:
        sess = db.get(SessionModel, token)
        if sess is not None:
            key = db.get(ApiKey, sess.api_key_id)
            if key is not None:
                key.status = "revoked"
            db.delete(sess)
            db.commit()
    response.delete_cookie(settings.SESSION_COOKIE_NAME)


@router.get("/me", response_model=MeResponse)
async def me(ctx: AuthContext = Depends(get_current_session)) -> MeResponse:
    return MeResponse(
        user_id=ctx.user.user_id,
        username=ctx.user.username,
        department=ctx.user.department,
        roles=ctx.roles,
    )
