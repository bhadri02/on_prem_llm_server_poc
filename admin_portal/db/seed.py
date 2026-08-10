"""
admin_portal/db/seed.py

Idempotent startup seeding for the users/roles/API-keys store (Phase 1).
Safe to call on every Admin Portal boot: every insert is guarded by an
existence check first, so restarts never duplicate rows or clobber edits an
admin has since made through the management API.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from admin_portal.db.keys import hash_key, key_prefix
from admin_portal.db.models import ApiKey, Role, RolePermission, User, UserRole
from admin_portal.db.passwords import hash_password

# Fixed UUID for the seed admin user — matches NEXT_FEATURES_PLAN.md Section 10.1
SEED_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000001"

_ROLES: dict[str, str] = {
    "viewer": "Read-only access",
    "analyst": "Chat and summarization",
    "developer": "Full task access, no admin",
    "admin": "Full access including user management",
}

# (role, task_type) pairs that are ALLOWED. Absence of a pair = deny.
# Matches the Section 2.3 policy matrix.
_ROLE_PERMISSIONS: list[tuple[str, str]] = [
    ("analyst", "chat"),
    ("analyst", "summarization"),
    ("analyst", "translation"),
    ("developer", "chat"),
    ("developer", "code"),
    ("developer", "reasoning"),
    ("developer", "summarization"),
    ("developer", "translation"),
    ("admin", "chat"),
    ("admin", "code"),
    ("admin", "reasoning"),
    ("admin", "summarization"),
    ("admin", "translation"),
]


def run_startup_seed(db: Session, gateway_api_key: str, seed_admin_password: str | None = None) -> None:
    """Insert roles, role_permissions, the seed admin user, and a key for the
    current GATEWAY_API_KEY value if they don't already exist.
    """
    _seed_roles(db)
    _seed_role_permissions(db)
    _seed_admin_user(db)
    _seed_legacy_key(db, gateway_api_key)
    if seed_admin_password:
        _seed_admin_password(db, seed_admin_password)
    db.commit()


def _seed_roles(db: Session) -> None:
    existing = {name for (name,) in db.query(Role.role_name).all()}
    for name, description in _ROLES.items():
        if name not in existing:
            db.add(Role(role_name=name, description=description))


def _seed_role_permissions(db: Session) -> None:
    existing = {
        (role_name, task_type)
        for role_name, task_type in db.query(RolePermission.role_name, RolePermission.task_type).all()
    }
    for role_name, task_type in _ROLE_PERMISSIONS:
        if (role_name, task_type) not in existing:
            db.add(RolePermission(role_name=role_name, task_type=task_type, allowed=True))


def _seed_admin_user(db: Session) -> None:
    user = db.get(User, SEED_ADMIN_USER_ID)
    if user is None:
        user = User(
            user_id=SEED_ADMIN_USER_ID,
            username="admin",
            email="admin@local",
            department="platform",
            status="active",
        )
        db.add(user)
        db.flush()

    has_admin_role = (
        db.query(UserRole).filter_by(user_id=SEED_ADMIN_USER_ID, role_name="admin").first() is not None
    )
    if not has_admin_role:
        db.add(UserRole(user_id=SEED_ADMIN_USER_ID, role_name="admin"))


def _seed_admin_password(db: Session, password: str) -> None:
    """Set a login password for the seed admin user, but ONLY if none is set
    yet — never overwrites a password an admin has since changed."""
    user = db.get(User, SEED_ADMIN_USER_ID)
    if user is not None and user.password_hash is None:
        user.password_hash = hash_password(password)


def _seed_legacy_key(db: Session, gateway_api_key: str) -> None:
    """Seed an api_keys row for whatever GATEWAY_API_KEY currently is, so the
    existing shared secret keeps working once the Gateway starts resolving
    keys through this DB instead of comparing against the raw env var.

    Deliberately left with no model_entitlements rows: empty entitlements
    means "all models" (backward-compat rule), which is the correct grant
    for an admin-role key rather than restricting it to a single model.
    """
    if not gateway_api_key:
        return
    digest = hash_key(gateway_api_key)
    existing = db.query(ApiKey).filter_by(key_hash=digest).first()
    if existing is not None:
        return
    db.add(
        ApiKey(
            user_id=SEED_ADMIN_USER_ID,
            key_hash=digest,
            key_prefix=key_prefix(gateway_api_key),
            label="Legacy POC key",
            status="active",
        )
    )
