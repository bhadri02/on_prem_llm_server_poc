"""
admin_portal/schemas/users.py

Pydantic schemas for user management endpoints (Phase 3).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str
    email: str | None = None
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    # Optional at creation — a user with no password set simply can't log in
    # yet until an admin sets one via PATCH /users/{id}/password.
    password: str | None = None


class UserOut(BaseModel):
    user_id: str
    username: str
    email: str | None = None
    department: str | None = None
    status: str
    roles: list[str]
    created_at: datetime
    updated_at: datetime


class UserRolesPatch(BaseModel):
    roles: list[str]


class UserStatusPatch(BaseModel):
    status: str  # active | inactive


class UserPasswordPatch(BaseModel):
    password: str
