"""
agent_framework/schemas/imf.py

Pydantic v2 models for the Internal Message Format (IMF).

The leaf blocks (IMFMessage, IMFUsage, IMFResponse, IMFGovernance,
IMFRouting, IMFCache) are re-exported from shared.imf (repo root) — see
that module's docstring for why those used to be a hand-maintained
per-service copy and why that was risky.

IMFUser/IMFRequest/IMFDocument stay defined here: this service requires
`user_id` (Requirement 10.1 — a missing user block must raise
ValidationError) and a non-empty `messages` list (Requirements 1.1, 1.3),
neither of which matches inference_adapter's deliberately lenient copy
(see inference_adapter/schemas/imf.py's docstring) closely enough to
share. Every class here sets extra="allow" so a field this file doesn't
know about survives this service's parse/dump round trip unchanged (see
shared.imf's docstring for why that matters); IMFUser also backfills
key_id/model_entitlements/rate_limit_override (previously missing here,
present in other services' copies) so those fields survive this hop even
though this service doesn't act on them itself.

Key validation rules (Requirements 1.1, 1.3, 1.4, 10.1):
  - IMFDocument.request_id must be a valid UUID v4.
  - IMFRequest.messages must contain at least one element.
  - IMFDocument.extensions is always present (defaults to {}), enabling
    safe access of extensions.get("agentic") in the router.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.imf import UUID4_RE as _UUID_V4_RE  # noqa: F401 — historical name
from shared.imf import (  # noqa: F401
    IMFCache,
    IMFGovernance,
    IMFMessage,
    IMFResponse,
    IMFRouting,
    IMFUsage,
)


class IMFUser(BaseModel):
    """Authenticated user context.

    Required fields (per Req 10.1): user_id. user_id has no default so a
    missing user block raises ValidationError.
    """

    model_config = ConfigDict(extra="allow")

    user_id: str
    department: str = ""
    roles: List[str] = Field(default_factory=list)
    auth_method: str = "api_key"
    key_id: Optional[str] = None
    model_entitlements: List[str] = Field(default_factory=list)
    rate_limit_override: Optional[int] = None


class IMFRequest(BaseModel):
    """The inbound request parameters.

    messages is required and must contain at least one element (Req 1.3, 10.1).
    """

    model_config = ConfigDict(extra="allow")

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
