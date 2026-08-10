"""
intelligent_router/models.py

Pydantic IMF models and request/response schemas for the Intelligent Router.
All models follow the platform Internal Message Format (IMF) contract.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# UUID-v4 compiled regex (case-insensitive)
# ---------------------------------------------------------------------------

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared message model
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: str


# ---------------------------------------------------------------------------
# IMF block models
# ---------------------------------------------------------------------------

class UserBlock(BaseModel):
    user_id: str
    department: str
    roles: list[str]
    auth_method: str
    # Populated by the API Gateway from the resolved API key profile
    # (Phase 2 — RBAC + per-user API keys). Read by the pipeline's
    # task-permission and model-entitlement checks.
    key_id: Optional[str] = None
    model_entitlements: list[str] = Field(default_factory=list)
    rate_limit_override: Optional[int] = None


class RequestBlock(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: Optional[str] = None
    task_type: Optional[str] = None
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


class GovernanceBlock(BaseModel):
    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list = Field(default_factory=list)  # str from security layer, dict in prod


class RoutingBlock(BaseModel):
    selected_model: Optional[str] = None
    routing_mode: str = "auto"
    fallback_level: int = 0


class CacheBlock(BaseModel):
    lookup_hit: bool = False
    cache_key: Optional[str] = None


class UsageBlock(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ResponseBlock(BaseModel):
    content: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: UsageBlock = Field(default_factory=UsageBlock)


# ---------------------------------------------------------------------------
# Top-level IMF request model
# ---------------------------------------------------------------------------

class IMFRequest(BaseModel):
    request_id: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    timestamp_utc: Optional[str] = None
    user: UserBlock
    request: RequestBlock
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)
    routing: RoutingBlock = Field(default_factory=RoutingBlock)
    cache: CacheBlock = Field(default_factory=CacheBlock)
    response: ResponseBlock = Field(default_factory=ResponseBlock)
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    @field_validator("request_id", mode="before")
    @classmethod
    def validate_request_id(cls, v: object) -> object:
        if not isinstance(v, str) or not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v


# ---------------------------------------------------------------------------
# OpenAI-compatible chat request
# ---------------------------------------------------------------------------

class OpenAIChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False
