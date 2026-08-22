from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    reason: Optional[str] = None  # present only when status = "degraded"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready", "degraded"]
    reason: Optional[str] = None  # present only when status != "ready"
