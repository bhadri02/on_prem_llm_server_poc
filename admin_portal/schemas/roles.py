"""
admin_portal/schemas/roles.py

Pydantic schemas for the read-only role/permission-matrix endpoints
(Phase 3 — Section 2.4: "Roles tab — read-only in POC").
"""

from __future__ import annotations

from pydantic import BaseModel


class RoleOut(BaseModel):
    role_name: str
    description: str | None = None


class RolePermissionsOut(BaseModel):
    role_name: str
    permissions: dict[str, bool]  # task_type -> allowed


class RolePermissionsPatch(BaseModel):
    permissions: dict[str, bool]  # task_type -> allowed; upserted, partial patches OK
