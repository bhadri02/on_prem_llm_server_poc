"""
agent_framework/schemas/imf.py

Pydantic v2 models for the Internal Message Format (IMF).

These models represent the canonical inter-layer communication envelope used
across the Enterprise On-Prem LLM Platform.  Every field mirrors the structure
defined in the platform master contract (00-platform-master-contract.md).

Key validation rules (Requirements 1.1, 1.3, 1.4, 10.1):
  - IMFDocument.request_id must be a valid UUID v4.
  - IMFRequest.messages must contain at least one element.
  - IMFDocument.extensions is always present (defaults to {}), enabling
    safe access of extensions.get("agentic") in the router.
"""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# UUID v4 regex — matches canonical lowercase hyphenated form.
# Pattern: xxxxxxxx-xxxx-4xxx-[89ab]xxx-xxxxxxxxxxxx
# ---------------------------------------------------------------------------
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class IMFMessage(BaseModel):
    """A single conversational message (role + content)."""

    role: str
    content: str


class IMFUsage(BaseModel):
    """Token-usage counters attached to a response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IMFResponse(BaseModel):
    """The LLM response block carried in the IMF envelope."""

    content: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: IMFUsage = Field(default_factory=IMFUsage)


class IMFGovernance(BaseModel):
    """Security & governance metadata populated by the Security Layer."""

    pii_masked: bool = False
    pii_fields_detected: List[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: List[Any] = Field(default_factory=list)


class IMFRouting(BaseModel):
    """Routing decisions populated by the Intelligent Router."""

    selected_model: Optional[str] = None
    routing_mode: str = "auto"
    fallback_level: int = 0


class IMFCache(BaseModel):
    """Cache lookup metadata."""

    lookup_hit: bool = False
    cache_key: Optional[str] = None


class IMFUser(BaseModel):
    """Authenticated user context.

    Required fields (per Req 10.1): user_id, department, roles, auth_method.
    user_id is required (no default) so a missing user block raises ValidationError.
    """

    user_id: str
    department: str = ""
    roles: List[str] = Field(default_factory=list)
    auth_method: str = "api_key"


class IMFRequest(BaseModel):
    """The inbound request parameters.

    messages is required and must contain at least one element (Req 1.3, 10.1).
    """

    model: Optional[str] = None
    task_type: Optional[str] = None
    # min_length=1 ensures an empty list raises a ValidationError (Req 1.3)
    messages: List[IMFMessage] = Field(..., min_length=1)
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7


# ---------------------------------------------------------------------------
# Top-level document
# ---------------------------------------------------------------------------


class IMFDocument(BaseModel):
    """Top-level IMF envelope.

    Validation:
      - request_id must match UUID v4 pattern (Req 1.4 / 10.1).
      - extensions defaults to {} so extensions.get("agentic") is always safe.

    ConfigDict(extra="allow") lets unknown top-level fields pass through so
    upstream layers can augment the envelope without breaking deserialization
    (Req 10.3, 10.4).
    """

    model_config = ConfigDict(extra="allow")

    request_id: str
    trace_id: str = ""
    span_id: str = ""
    timestamp_utc: str = ""

    user: IMFUser
    request: IMFRequest

    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    routing: IMFRouting = Field(default_factory=IMFRouting)
    cache: IMFCache = Field(default_factory=IMFCache)
    response: IMFResponse = Field(default_factory=IMFResponse)

    metadata: Dict[str, Any] = Field(default_factory=dict)
    # extensions MUST be present so callers can safely do
    # imf.get("extensions", {}).get("agentic") or body.extensions.get("agentic")
    extensions: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def validate_request_id_uuid_v4(cls, v: str) -> str:
        """Ensure request_id is a lowercase UUID v4 string (Req 1.4, 10.1)."""
        if not _UUID_V4_RE.match(v):
            raise ValueError(
                f"request_id must be a valid UUID v4 "
                f"(e.g. '550e8400-e29b-41d4-a716-446655440000'), got: {v!r}"
            )
        return v
