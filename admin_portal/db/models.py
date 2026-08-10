"""
admin_portal/db/models.py

SQLAlchemy declarative models for the users/roles/API-keys store owned by
the Admin Portal API (Phase 1 — Persistent DB + RBAC).

Schema matches NEXT_FEATURES_PLAN.md Section 10, with two portability
adjustments so the same models run unchanged against SQLite in tests:
  - Primary keys are String(36) UUIDs generated in Python (not Postgres'
    gen_random_uuid()).
  - Timestamps default to Python-side UTC `now()` (not SQL `NOW()`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | inactive
    # Nullable: existing rows predate login (Phase 6) and simply can't log in
    # until an admin sets one via PATCH /users/{id}/password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Role(Base):
    __tablename__ = "roles"

    role_name: Mapped[str] = mapped_column(String(50), primary_key=True)  # viewer | analyst | developer | admin
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    role_name: Mapped[str] = mapped_column(
        String(50), ForeignKey("roles.role_name", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="roles")


class ApiKey(Base):
    __tablename__ = "api_keys"

    key_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 hex digest
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | revoked | expired
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="api_keys")
    model_entitlements: Mapped[list["KeyModelEntitlement"]] = relationship(
        back_populates="api_key", cascade="all, delete-orphan"
    )


class KeyModelEntitlement(Base):
    __tablename__ = "key_model_entitlements"

    key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.key_id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    api_key: Mapped["ApiKey"] = relationship(back_populates="model_entitlements")


class Session(Base):
    """Browser login session (Phase 6). The session_id itself is the bearer
    token stored as an httpOnly cookie — same trust model as an API key
    (high-entropy random value, DB-checked, revocable), just short-lived.

    On login, a real ApiKey row is also minted for the user (api_key_id) so
    the existing api_gateway <-> admin_portal /portal/keys/resolve contract
    needs zero changes — a session-derived request is indistinguishable from
    a manually-created-key request to the rest of the pipeline. api_key_raw
    is a deliberate, scoped exception to "never store raw keys": it only
    lives as long as the session (deleted on logout/expiry), used solely by
    admin_portal's own chat proxy to act on the logged-in user's behalf.
    """

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.key_id", ondelete="CASCADE"), nullable=False
    )
    api_key_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_name: Mapped[str] = mapped_column(
        String(50), ForeignKey("roles.role_name", ondelete="CASCADE"), primary_key=True
    )
    task_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
