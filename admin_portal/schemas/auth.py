from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    user_id: str
    username: str
    department: str | None = None
    roles: list[str]
