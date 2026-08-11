from enum import Enum
from pydantic import BaseModel, Field, field_validator
import re


class LayerEnum(str, Enum):
    api_gateway = "api_gateway"
    security    = "security"
    router      = "router"
    cache       = "cache"
    inference   = "inference"
    agent       = "agent"


class EventTypeEnum(str, Enum):
    request_received    = "request_received"
    auth_pass           = "auth_pass"
    auth_fail           = "auth_fail"
    security_block      = "security_block"
    cache_hit           = "cache_hit"
    inference_start     = "inference_start"
    inference_complete  = "inference_complete"
    response_sent       = "response_sent"
    policy_denied       = "policy_denied"
    model_not_entitled  = "model_not_entitled"


class OutcomeEnum(str, Enum):
    pass_  = "pass"
    block  = "block"
    flag   = "flag"


UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE
)


class AuditEventCreate(BaseModel):
    audit_id:          str | None = None
    request_id:        str
    timestamp_utc:     str | None = None
    user_id:           str | None = None
    department:        str | None = None
    layer:             LayerEnum
    event_type:        EventTypeEnum
    model_used:        str | None = None
    prompt_tokens:     int = 0
    completion_tokens: int = 0
    latency_ms:        int = 0
    outcome:           OutcomeEnum
    error_code:        str | None = None
    pii_actions:       list = Field(default_factory=list)
    policy_decisions:  list = Field(default_factory=list)

    @field_validator("request_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v


class AuditEventResponse(AuditEventCreate):
    audit_id:      str
    timestamp_utc: str


class BatchWriteRequest(BaseModel):
    events: list[AuditEventCreate] = Field(min_length=1, max_length=500)


class BatchWriteResponse(BaseModel):
    inserted:  int
    audit_ids: list[str]


class SummaryResponse(BaseModel):
    total_events: int
    by_outcome:   dict[str, int]
    by_layer:     dict[str, int]


class TokenUsage(BaseModel):
    prompt_tokens:     int
    completion_tokens: int
    total_tokens:      int


class GovernanceSummaryResponse(BaseModel):
    total_events:            int
    by_outcome:              dict[str, int]
    by_layer:                dict[str, int]
    requests_blocked_total:  int
    blocked_by_reason:       dict[str, int]
    injection_flagged_total: int
    pii_detections_total:    int
    token_usage:             TokenUsage
    model_usage:             dict[str, int]
