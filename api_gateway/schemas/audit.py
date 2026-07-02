"""Audit event schema for the API Gateway layer."""

from typing import Literal

from pydantic import BaseModel


class AuditEvent(BaseModel):
    audit_id: str           # UUID v4, unique per event
    request_id: str         # UUID v4, matches IMF request_id
    timestamp_utc: str      # ISO-8601 UTC
    user_id: str | None = None
    department: str | None = None
    layer: Literal["api_gateway"] = "api_gateway"
    event_type: str         # request_received | auth_pass | auth_fail | rate_limited | response_sent
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    outcome: str            # "pass" | "block" | "error"
    reason: str | None = None   # for auth_fail: "missing_header" | "key_mismatch"
    error_code: str | None = None
