"""
intelligent_router/models.py

Pydantic IMF models and request/response schemas for the Intelligent Router.

The leaf blocks that matched shared.imf's canonical shape closely enough to
unify safely (Message, GovernanceBlock, RoutingBlock, CacheBlock,
UsageBlock, ResponseBlock) are now aliases of shared.imf's definitions —
see that module's docstring for why those used to be a hand-maintained
per-service copy and why that was risky.

UserBlock/RequestBlock/the top-level document stay defined here: this is
the *strictest* service in the platform — UserBlock requires all four
identity fields with no defaults (user_id, department, roles, auth_method),
and the top-level document requires `user` itself with no default — a
deliberate strictness no other service's copy shares (e.g. api_gateway
defaults every one of those fields, since it's the one service
*constructing* the envelope rather than parsing one someone else built).
Every class here sets extra="allow" so a field this file doesn't know
about survives this service's parse/dump round trip unchanged (see
shared.imf's docstring for why that matters).

This module's own top-level document was confusingly named ``IMFRequest``
— colliding with every *other* service's name for the nested *request*
block — while what other services call the top-level ``IMFDocument`` lives
here as ``IMFRequest``. That naming is preserved below purely for
backward compatibility with existing call sites in this service; new code
should prefer being explicit about which one it means.

OpenAIChatRequest is intelligent_router-specific (the OpenAI-compatible
/v1/chat/completions-shaped request), not part of the IMF envelope, and
remains defined here.
"""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.imf import UUID4_RE  # noqa: F401 — re-exported for existing callers
from shared.imf import IMFCache as CacheBlock
from shared.imf import IMFGovernance as GovernanceBlock
from shared.imf import IMFMessage as Message
from shared.imf import IMFResponse as ResponseBlock
from shared.imf import IMFRouting as RoutingBlock
from shared.imf import IMFUsage as UsageBlock


# ---------------------------------------------------------------------------
# Blocks that keep their own definition (see module docstring for why)
# ---------------------------------------------------------------------------

class UserBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

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
    model_config = ConfigDict(extra="allow")

    messages: list[Message] = Field(min_length=1)
    model: Optional[str] = None
    task_type: Optional[str] = None
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


# ---------------------------------------------------------------------------
# Top-level IMFRequest (== the whole envelope — see module docstring)
# ---------------------------------------------------------------------------

class IMFRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

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
# OpenAI-compatible chat request (intelligent_router-specific)
# ---------------------------------------------------------------------------

class OpenAIChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False
