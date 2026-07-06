from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    audit_id: str
    request_id: str
    timestamp_utc: str  # ISO-8601
    user_id: Optional[str] = None
    department: Optional[str] = None
    model_used: Optional[str] = None
    layer: str
    event_type: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    pii_actions: List[str] = Field(default_factory=list)
    policy_decisions: List[str] = Field(default_factory=list)
    outcome: str  # pass | block | flag | fallback
    error_code: Optional[str] = None


class AuditEventList(BaseModel):
    events: List[AuditEvent]
