"""
models.py — Pydantic IMF models and audit event payloads for the
Security & Governance Layer.

Covers:
- UUID4_RE compiled regex for UUID-v4 validation
- Message, UserBlock, RequestBlock, GovernanceBlock, ResponseBlock
- IMFRequest (the top-level Internal Message Format envelope)
- PreAuditEventPayload and PostAuditEventPayload for Audit Store writes
"""

import re

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# 4.1  UUID-v4 regex
# ---------------------------------------------------------------------------

UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 4.2  Message
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A single chat message with role and content."""

    role: str
    content: str


# ---------------------------------------------------------------------------
# 4.3  UserBlock
# ---------------------------------------------------------------------------

class UserBlock(BaseModel):
    """Optional caller identity block carried inside the IMF."""

    user_id: str | None = None
    department: str | None = None
    roles: list[str] | None = None
    auth_method: str | None = None
    # Populated by the API Gateway from the resolved API key profile
    # (Phase 2 — RBAC + per-user API keys). The Security Layer doesn't act
    # on these itself — they must simply survive this model's parse/dump
    # round-trip so the Intelligent Router (which does enforce them) still
    # receives them.
    key_id: str | None = None
    model_entitlements: list[str] = Field(default_factory=list)
    rate_limit_override: int | None = None


# ---------------------------------------------------------------------------
# 4.4  RequestBlock
# ---------------------------------------------------------------------------

class RequestBlock(BaseModel):
    """The request payload block inside the IMF."""

    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    task_type: str | None = None
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7


# ---------------------------------------------------------------------------
# 4.5  GovernanceBlock
# ---------------------------------------------------------------------------

class GovernanceBlock(BaseModel):
    """Governance enrichment block populated by the Security Layer."""

    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4.6  ResponseBlock
# ---------------------------------------------------------------------------

class ResponseBlock(BaseModel):
    """The response payload block inside the IMF."""

    content: str | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# 4.7  IMFRequest
# ---------------------------------------------------------------------------

class RoutingBlock(BaseModel):
    """Routing decision block — populated by the Router layer."""

    selected_model: str | None = None
    routing_mode: str = "auto"
    fallback_level: int = 0


class CacheBlock(BaseModel):
    """Cache lookup/write state block."""

    lookup_hit: bool = False
    cache_key: str | None = None


class IMFRequest(BaseModel):
    """Top-level Internal Message Format (IMF) envelope.

    ``request_id`` must be a valid UUID-v4; construction raises
    ``ValidationError`` (wrapping ``ValueError``) for any other value.

    All fields align with the router's IMFRequest schema so the enriched
    IMF can be forwarded directly without transformation.
    """

    request_id: str
    trace_id: str | None = None
    span_id: str | None = None
    timestamp_utc: str | None = None
    user: UserBlock | None = None
    request: RequestBlock
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)
    routing: RoutingBlock = Field(default_factory=RoutingBlock)
    cache: CacheBlock = Field(default_factory=CacheBlock)
    response: ResponseBlock | None = None
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, v: str) -> str:
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v


# ---------------------------------------------------------------------------
# 4.8  Audit event payloads
# ---------------------------------------------------------------------------

class PreAuditEventPayload(BaseModel):
    """Payload sent to the Audit Store after the pre-generation pipeline."""

    request_id: str
    user_id: str | None = None
    layer: str = "security"
    event_type: str
    outcome: str
    timestamp_utc: str
    latency_ms: int
    pii_actions: list[str] = Field(default_factory=list)
    policy_decisions: list[str] = Field(default_factory=list)


class PostAuditEventPayload(BaseModel):
    """Payload sent to the Audit Store after the post-generation pipeline."""

    request_id: str
    user_id: str | None = None
    layer: str = "security"
    event_type: str
    outcome: str
    timestamp_utc: str
    latency_ms: int
    pii_actions: list[str] = Field(default_factory=list)
    policy_decisions: list[str] = Field(default_factory=list)
