"""
admin_portal/routers/roles.py

Role + permission-matrix endpoints for the Admin Portal API.

GET  /roles/, GET /roles/{role}/permissions — Phase 3, read-only.
PATCH /roles/{role}/permissions             — Phase 5, editable (admin
    mockup's "Role permission matrix" panel).

This PATCH updates the `role_permissions` DB table. `intelligent_router`
polls this table live (see intelligent_router/services/policy_resolver.py)
on a TTL cache (POLICY_CACHE_TTL_SECONDS, default 15s) — a change here takes
effect on real request enforcement within that window, with NO Router
restart required. `policy_matrix.yaml` still exists as the fail-fast
startup baseline and the offline fallback if admin_portal is ever
unreachable, but it is no longer the Router's ongoing source of truth.

Separate, unrelated gate: `security_layer`'s coarse "can this identity call
the platform at all" check (ALLOWED_ROLES in security_layer/policy.py) is
still a hardcoded frozenset in Python, not backed by this table at all. A
role excluded there (currently just "viewer") is rejected before it ever
reaches this fine-grained matrix, regardless of what's granted here — so
granting a denied task to "viewer" specifically will appear to do nothing.
Every other role (analyst/developer/admin) passes that gate and is fully
governed by this table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin_portal.db.models import Role, RolePermission
from admin_portal.db.session import get_db
from admin_portal.schemas.roles import RoleOut, RolePermissionsOut, RolePermissionsPatch
from admin_portal.services.session_auth import get_current_session, require_admin

router = APIRouter(tags=["roles"], dependencies=[Depends(get_current_session)])


@router.get("/roles/", response_model=list[RoleOut])
async def list_roles(db: Session = Depends(get_db)) -> list[RoleOut]:
    roles = db.query(Role).order_by(Role.role_name).all()
    return [RoleOut(role_name=r.role_name, description=r.description) for r in roles]


@router.get("/roles/{role}/permissions", response_model=RolePermissionsOut)
async def get_role_permissions(role: str, db: Session = Depends(get_db)) -> RolePermissionsOut:
    if db.get(Role, role) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Role '{role}' not found."},
        )

    rows = db.execute(
        select(RolePermission.task_type, RolePermission.allowed).where(
            RolePermission.role_name == role
        )
    ).all()
    permissions = {task_type: allowed for task_type, allowed in rows}
    return RolePermissionsOut(role_name=role, permissions=permissions)


@router.patch(
    "/roles/{role}/permissions",
    response_model=RolePermissionsOut,
    summary="Update a role's task-type permissions (live within ~15s — see module docstring)",
    dependencies=[Depends(require_admin)],
)
async def patch_role_permissions(
    role: str, body: RolePermissionsPatch, db: Session = Depends(get_db)
) -> RolePermissionsOut:
    if db.get(Role, role) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Role '{role}' not found."},
        )

    for task_type, allowed in body.permissions.items():
        row = db.get(RolePermission, (role, task_type))
        if row is None:
            db.add(RolePermission(role_name=role, task_type=task_type, allowed=allowed))
        else:
            row.allowed = allowed
    db.commit()

    rows = db.execute(
        select(RolePermission.task_type, RolePermission.allowed).where(
            RolePermission.role_name == role
        )
    ).all()
    permissions = {task_type: allowed for task_type, allowed in rows}
    return RolePermissionsOut(role_name=role, permissions=permissions)
