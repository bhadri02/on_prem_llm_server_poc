"""
admin_portal/routers/users.py

User + API key management endpoints for the Admin Portal API (Phase 3).

No new browser-facing auth on these endpoints: every existing /portal/*
endpoint (Playground, Models, Audit) is already callable with zero auth
from the browser, since Portal UI has no login system. This matches that
existing posture rather than introducing one. (The one endpoint that does
carry a real auth check is GET /keys/resolve — see routers/keys.py — since
that one is service-to-service, not browser-facing.)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin_portal.db.keys import generate_raw_key, hash_key, key_prefix
from admin_portal.db.models import ApiKey, KeyModelEntitlement, Role, User, UserRole
from admin_portal.db.passwords import hash_password
from admin_portal.db.session import get_db
from admin_portal.schemas.keys import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyModelsPatch,
    ApiKeyOut,
    ApiKeyRateLimitPatch,
)
from admin_portal.schemas.users import (
    UserCreate,
    UserOut,
    UserPasswordPatch,
    UserRolesPatch,
    UserStatusPatch,
)
from admin_portal.services.session_auth import require_admin

router = APIRouter(tags=["users"], dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _user_roles(db: Session, user_id: str) -> list[str]:
    return [
        role_name
        for (role_name,) in db.execute(
            select(UserRole.role_name).where(UserRole.user_id == user_id)
        ).all()
    ]


def _to_user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        department=user.department,
        status=user.status,
        roles=_user_roles(db, user.user_id),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"User '{user_id}' not found."},
        )
    return user


def _validate_roles_exist(db: Session, role_names: list[str]) -> None:
    for role_name in role_names:
        if db.get(Role, role_name) is None:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_role", "message": f"Unknown role '{role_name}'."},
            )


def _to_key_out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        key_id=key.key_id,
        key_prefix=key.key_prefix,
        label=key.label,
        status=key.status,
        expires_at=key.expires_at,
        rate_limit_rpm=key.rate_limit_rpm,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        model_entitlements=[e.model_name for e in key.model_entitlements],
    )


def _get_key_or_404(db: Session, user_id: str, key_id: str) -> ApiKey:
    key = db.query(ApiKey).filter_by(user_id=user_id, key_id=key_id).first()
    if key is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Key '{key_id}' not found for user '{user_id}'.",
            },
        )
    return key


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


@router.post("/users/", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    if db.query(User).filter_by(username=body.username).first() is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_exists",
                "message": f"Username '{body.username}' is already taken.",
            },
        )
    _validate_roles_exist(db, body.roles)

    user = User(
        username=body.username,
        email=body.email,
        department=body.department,
        password_hash=hash_password(body.password) if body.password else None,
    )
    db.add(user)
    db.flush()
    for role_name in body.roles:
        db.add(UserRole(user_id=user.user_id, role_name=role_name))
    db.commit()
    db.refresh(user)
    return _to_user_out(db, user)


@router.get("/users/", response_model=list[UserOut])
async def list_users(db: Session = Depends(get_db)) -> list[UserOut]:
    users = db.query(User).order_by(User.created_at).all()
    return [_to_user_out(db, u) for u in users]


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: str, db: Session = Depends(get_db)) -> UserOut:
    return _to_user_out(db, _get_user_or_404(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user_status(
    user_id: str, body: UserStatusPatch, db: Session = Depends(get_db)
) -> UserOut:
    user = _get_user_or_404(db, user_id)
    if body.status not in ("active", "inactive"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": "status must be 'active' or 'inactive'.",
                "allowed_values": ["active", "inactive"],
            },
        )
    user.status = body.status
    db.commit()
    db.refresh(user)
    return _to_user_out(db, user)


@router.patch("/users/{user_id}/roles", response_model=UserOut)
async def replace_user_roles(
    user_id: str, body: UserRolesPatch, db: Session = Depends(get_db)
) -> UserOut:
    user = _get_user_or_404(db, user_id)
    _validate_roles_exist(db, body.roles)

    db.query(UserRole).filter_by(user_id=user_id).delete()
    for role_name in body.roles:
        db.add(UserRole(user_id=user_id, role_name=role_name))
    db.commit()
    db.refresh(user)
    return _to_user_out(db, user)


@router.delete("/users/{user_id}", status_code=204, response_model=None)
async def deactivate_user(user_id: str, db: Session = Depends(get_db)) -> None:
    user = _get_user_or_404(db, user_id)
    user.status = "inactive"
    db.commit()


@router.patch("/users/{user_id}/password", response_model=UserOut)
async def reset_user_password(
    user_id: str, body: UserPasswordPatch, db: Session = Depends(get_db)
) -> UserOut:
    """Admin-set/reset a user's login password (Phase 6)."""
    user = _get_user_or_404(db, user_id)
    user.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(user)
    return _to_user_out(db, user)


# ---------------------------------------------------------------------------
# API key management — nested under /users/{user_id}/keys
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    user_id: str, body: ApiKeyCreate, db: Session = Depends(get_db)
) -> ApiKeyCreated:
    """Generate a new API key for the user. The raw key is returned exactly
    once, in this response — it is never stored or retrievable again."""
    _get_user_or_404(db, user_id)

    raw_key = generate_raw_key()
    key = ApiKey(
        user_id=user_id,
        key_hash=hash_key(raw_key),
        key_prefix=key_prefix(raw_key),
        label=body.label,
        expires_at=body.expires_at,
        rate_limit_rpm=body.rate_limit_rpm,
    )
    db.add(key)
    db.flush()
    for model_name in body.model_entitlements:
        db.add(KeyModelEntitlement(key_id=key.key_id, model_name=model_name))
    db.commit()
    db.refresh(key)

    return ApiKeyCreated(**_to_key_out(key).model_dump(), raw_key=raw_key)


@router.get("/users/{user_id}/keys", response_model=list[ApiKeyOut])
async def list_keys(user_id: str, db: Session = Depends(get_db)) -> list[ApiKeyOut]:
    _get_user_or_404(db, user_id)
    keys = db.query(ApiKey).filter_by(user_id=user_id).order_by(ApiKey.created_at).all()
    return [_to_key_out(k) for k in keys]


@router.delete("/users/{user_id}/keys/{key_id}", response_model=ApiKeyOut)
async def revoke_key(user_id: str, key_id: str, db: Session = Depends(get_db)) -> ApiKeyOut:
    key = _get_key_or_404(db, user_id, key_id)
    key.status = "revoked"
    db.commit()
    db.refresh(key)
    return _to_key_out(key)


@router.patch("/users/{user_id}/keys/{key_id}/models", response_model=ApiKeyOut)
async def patch_key_models(
    user_id: str, key_id: str, body: ApiKeyModelsPatch, db: Session = Depends(get_db)
) -> ApiKeyOut:
    key = _get_key_or_404(db, user_id, key_id)
    db.query(KeyModelEntitlement).filter_by(key_id=key_id).delete()
    for model_name in body.model_entitlements:
        db.add(KeyModelEntitlement(key_id=key_id, model_name=model_name))
    db.commit()
    db.refresh(key)
    return _to_key_out(key)


@router.patch("/users/{user_id}/keys/{key_id}/rate-limit", response_model=ApiKeyOut)
async def patch_key_rate_limit(
    user_id: str, key_id: str, body: ApiKeyRateLimitPatch, db: Session = Depends(get_db)
) -> ApiKeyOut:
    """Change a key's own request-per-minute limit.

    Rate limiting is enforced entirely per-key (api_gateway's
    RateLimitMiddleware reads only the resolved key's own rate_limit_rpm,
    never a shared/global fallback), so this is the only way to raise or
    lower a specific key's throughput after it's already been issued.
    """
    key = _get_key_or_404(db, user_id, key_id)
    key.rate_limit_rpm = body.rate_limit_rpm
    db.commit()
    db.refresh(key)
    return _to_key_out(key)
