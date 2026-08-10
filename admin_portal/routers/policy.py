"""
admin_portal/routers/policy.py

Internal (service-to-service) endpoint that lets intelligent_router pull the
live (role, task_type) permission matrix from the database on a TTL cache,
instead of only ever reading the static policy_matrix.yaml loaded once at
its own startup. This is what makes PATCH /portal/roles/{role}/permissions
changes take effect on real request routing within a bounded staleness
window, rather than requiring a hand-edited YAML file + a Router restart —
the gap previously documented in routers/roles.py and
docs/FRONTEND_INTEGRATION.md.

Guarded by the same X-Portal-Internal-Key mechanism as /portal/keys/resolve
— never session-gated, since intelligent_router has no login session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from admin_portal.db.models import RolePermission
from admin_portal.db.session import get_db
from admin_portal.routers.keys import _require_internal_key

router = APIRouter(tags=["policy"], dependencies=[Depends(_require_internal_key)])


@router.get(
    "/policy/matrix",
    summary="[internal] Full (role, task_type) permission matrix",
    description=(
        "Returns every role's task-type permissions in one call — "
        "{role_name: {task_type: allowed}} — sourced live from the "
        "role_permissions table. Polled by intelligent_router on a TTL "
        "cache (see intelligent_router/services/policy_resolver.py) so "
        "admin-edited permissions take effect without a Router restart."
    ),
)
async def get_policy_matrix(db: Session = Depends(get_db)) -> dict[str, dict[str, bool]]:
    rows = db.execute(
        select(RolePermission.role_name, RolePermission.task_type, RolePermission.allowed)
    ).all()

    matrix: dict[str, dict[str, bool]] = {}
    for role_name, task_type, allowed in rows:
        matrix.setdefault(role_name, {})[task_type] = allowed
    return matrix
