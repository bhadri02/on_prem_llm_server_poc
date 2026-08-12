"""
admin_portal/schemas/keys.py

Pydantic schemas for API key management and internal key resolution
(Phase 1 — Persistent DB; Phase 3 — key management endpoints).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from admin_portal.db.models import DEFAULT_RATE_LIMIT_RPM


class KeyResolveResponse(BaseModel):
    """Returned by GET /portal/keys/resolve to the API Gateway."""

    user_id: str
    username: str
    department: str | None = None
    roles: list[str]
    model_entitlements: list[str] = Field(default_factory=list)
    key_id: str
    rate_limit_override: int


class ApiKeyCreate(BaseModel):
    label: str | None = None
    model_entitlements: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    rate_limit_rpm: int = Field(default=DEFAULT_RATE_LIMIT_RPM, gt=0)


class ApiKeyOut(BaseModel):
    key_id: str
    key_prefix: str
    label: str | None = None
    status: str
    expires_at: datetime | None = None
    rate_limit_rpm: int
    created_at: datetime
    last_used_at: datetime | None = None
    model_entitlements: list[str] = Field(default_factory=list)


class ApiKeyCreated(ApiKeyOut):
    """Returned only at creation time — includes the raw key value once."""

    raw_key: str


class ApiKeyModelsPatch(BaseModel):
    model_entitlements: list[str]


class ApiKeyRateLimitPatch(BaseModel):
    rate_limit_rpm: int = Field(gt=0)


class ApiKeyWithOwner(ApiKeyOut):
    """ApiKeyOut plus owner identity — used by the admin-wide GET /portal/keys/
    listing (Section: admin mockup's "API Keys" tab), which spans all users
    rather than being scoped to one via /portal/users/{id}/keys."""

    user_id: str
    owner_username: str
