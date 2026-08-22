"""
models.py — Pydantic IMF models and audit event payloads for the
Security & Governance Layer.

The leaf blocks that matched shared.imf's canonical shape closely enough
to unify safely (Message, GovernanceBlock, ResponseBlock, RoutingBlock,
CacheBlock) are now aliases of shared.imf's definitions — see that
module's docstring for why those used to be a hand-maintained per-service
copy and why that was risky.

UserBlock/RequestBlock/the top-level document stay defined here: this
service's UserBlock is fully optional (every field defaults to None),
which doesn't match any other service's IMFUser closely enough to share,
and RequestBlock requires a non-empty `messages` at parse time (min_length=1)
— a deliberate strictness inference_adapter's own copy explicitly does NOT
want (see inference_adapter/schemas/imf.py's docstring). Every class here
sets extra="allow" so a field this file doesn't know about survives this
service's parse/dump round trip unchanged (see shared.imf's docstring for
why that matters).

This module's own top-level document was confusingly named ``IMFRequest``
— colliding with every *other* service's name for the nested *request*
block — while what other services call the top-level ``IMFDocument`` lives
here as ``IMFRequest``. That naming is preserved below purely for
backward compatibility with existing call sites in this service; new code
should prefer being explicit about which one it means.

Also covers PreAuditEventPayload and PostAuditEventPayload for Audit Store
writes — these are security_layer-specific, not part of the IMF envelope,
and remain defined here.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.imf import UUID4_RE  # noqa: F401 — re-exported for existing callers
from shared.imf import IMFCache as CacheBlock
from shared.imf import IMFGovernance as GovernanceBlock
from shared.imf import IMFMessage as Message
from shared.imf import IMFResponse as ResponseBlock
from shared.imf import IMFRouting as RoutingBlock


# ---------------------------------------------------------------------------
# Blocks that keep their own definition (see module docstring for why)
# ---------------------------------------------------------------------------

class UserBlock(BaseModel):
    """Optional caller identity block carried inside the IMF."""

    model_config = ConfigDict(extra="allow")

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


class RequestBlock(BaseModel):
    """The request payload block inside the IMF."""

    model_config = ConfigDict(extra="allow")

    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    task_type: str | None = None
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7


# ---------------------------------------------------------------------------
# Top-level IMFRequest (== the whole envelope — see module docstring)
# ---------------------------------------------------------------------------

class IMFRequest(BaseModel):
    """Top-level Internal Message Format (IMF) envelope.

    ``request_id`` must be a valid UUID-v4; construction raises
    ``ValidationError`` (wrapping ``ValueError``) for any other value.

    All fields align with the router's IMFRequest schema so the enriched
    IMF can be forwarded directly without transformation.
    """

    model_config = ConfigDict(extra="allow")

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
# Audit event payloads (security_layer-specific — not part of the IMF envelope)
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
