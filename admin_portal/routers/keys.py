"""
admin_portal/routers/keys.py

Internal key-resolution endpoint for the Admin Portal API (Phase 1).

GET /keys/resolve — used by the API Gateway on every authenticated request
to resolve an inbound X-Api-Key into a user profile + entitlements. This is
service-to-service only: it is guarded by a shared secret
(ADMIN_PORTAL_INTERNAL_KEY), never exposed to the browser.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin_portal.config import settings
from admin_portal.db.keys import hash_key
from admin_portal.db.models import ApiKey, KeyModelEntitlement, User, UserRole
from admin_portal.db.session import get_db
from admin_portal.schemas.keys import ApiKeyWithOwner, KeyResolveResponse
from admin_portal.services.session_auth import require_admin

router = APIRouter(tags=["keys"])


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < datetime.now(timezone.utc)


def _require_internal_key(x_portal_internal_key: str | None = Header(default=None)) -> None:
    if not x_portal_internal_key or x_portal_internal_key != settings.ADMIN_PORTAL_INTERNAL_KEY:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


@router.get(
    "/keys/resolve",
    response_model=KeyResolveResponse,
    dependencies=[Depends(_require_internal_key)],
    summary="Resolve an API key to its user profile and entitlements",
    description=(
        "Internal endpoint used by the API Gateway. Requires the "
        "X-Portal-Internal-Key header. Returns 404 when the key is not "
        "found, revoked, expired, or its owning user is inactive."
    ),
)
async def resolve_key(key: str, db: Session = Depends(get_db)) -> KeyResolveResponse:
    digest = hash_key(key)

    api_key = db.execute(select(ApiKey).where(ApiKey.key_hash == digest)).scalar_one_or_none()
    if api_key is None or api_key.status != "active" or _is_expired(api_key.expires_at):
        raise HTTPException(status_code=404, detail={"error": "key_not_found"})

    user = db.get(User, api_key.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=404, detail={"error": "key_not_found"})

    roles = [
        role_name
        for (role_name,) in db.execute(
            select(UserRole.role_name).where(UserRole.user_id == user.user_id)
        ).all()
    ]
    entitlements = [
        model_name
        for (model_name,) in db.execute(
            select(KeyModelEntitlement.model_name).where(KeyModelEntitlement.key_id == api_key.key_id)
        ).all()
    ]

    return KeyResolveResponse(
        user_id=user.user_id,
        username=user.username,
        department=user.department,
        roles=roles,
        model_entitlements=entitlements,
        key_id=api_key.key_id,
        rate_limit_override=api_key.rate_limit_rpm,
    )


# ---------------------------------------------------------------------------
# GET /keys/  — admin-wide key listing (Phase 5 — admin mockup "API Keys" tab)
#
# Distinct from /portal/users/{id}/keys (Phase 3), which is scoped to one
# user. This one spans every user, with owner identity joined in, matching
# the admin console's flat "API Keys" table.
# ---------------------------------------------------------------------------


@router.get(
    "/keys/",
    response_model=list[ApiKeyWithOwner],
    summary="List every API key across all users",
    dependencies=[Depends(require_admin)],
)
async def list_all_keys(db: Session = Depends(get_db)) -> list[ApiKeyWithOwner]:
    rows = (
        db.execute(select(ApiKey, User.username).join(User, ApiKey.user_id == User.user_id))
        .all()
    )

    result: list[ApiKeyWithOwner] = []
    for api_key, username in rows:
        entitlements = [
            model_name
            for (model_name,) in db.execute(
                select(KeyModelEntitlement.model_name).where(
                    KeyModelEntitlement.key_id == api_key.key_id
                )
            ).all()
        ]
        result.append(
            ApiKeyWithOwner(
                key_id=api_key.key_id,
                key_prefix=api_key.key_prefix,
                label=api_key.label,
                status=api_key.status,
                expires_at=api_key.expires_at,
                rate_limit_rpm=api_key.rate_limit_rpm,
                created_at=api_key.created_at,
                last_used_at=api_key.last_used_at,
                model_entitlements=entitlements,
                user_id=api_key.user_id,
                owner_username=username,
            )
        )
    return result
