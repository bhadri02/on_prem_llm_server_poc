"""
Pydantic schemas for the Internal Message Format (IMF) as consumed by the Inference Adapter.

The leaf blocks (IMFMessage, IMFUsage, IMFResponse, IMFGovernance,
IMFRouting, IMFCache) are re-exported from shared.imf — see that module's
docstring for why those used to be a hand-maintained per-service copy and
why that was risky.

IMFUser/IMFRequest/IMFDocument stay defined here, all fields optional with
sensible defaults, so that the Inference Adapter can provide structured
422 errors for missing/invalid required fields (e.g. an explicit empty
`request.messages`, or an absent `routing.selected_model`) rather than
letting Pydantic raise its own differently-shaped validation error at
parse time — this is load-bearing for tests/inference_adapter's structured
error-event assertions, not incidental laxity. Every class here sets
extra="allow" so a field this file doesn't know about survives this
service's parse/dump round trip unchanged (see shared.imf's docstring for
why that matters); IMFUser also backfills key_id/model_entitlements/
rate_limit_override (previously missing here, present in other services'
copies) so those fields survive this hop even though this service doesn't
act on them itself.

All fields outside `response`, `metadata`, and `extensions` are preserved
unchanged when the Inference Adapter returns the outbound IMF document.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shared.imf import (  # noqa: F401
    IMFCache,
    IMFGovernance,
    IMFMessage,
    IMFResponse,
    IMFRouting,
    IMFUsage,
)


class IMFUser(BaseModel):
    """Caller identity carried in the IMF envelope."""

    model_config = ConfigDict(extra="allow")

    user_id: str | None = None
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    auth_method: str | None = None
    key_id: str | None = None
    model_entitlements: list[str] = Field(default_factory=list)
    rate_limit_override: int | None = None


class IMFRequest(BaseModel):
    """
    The consumer's inbound request fields carried inside the IMF envelope.

    All fields are optional with sensible defaults so that the Inference
    Adapter can provide structured 422 errors for missing required fields
    rather than letting Pydantic raise a validation error at parse time.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = Field(default_factory=list)
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class IMFDocument(BaseModel):
    """
    The full IMF envelope accepted by POST /infer.

    Every top-level field defaults to an appropriate empty value so that the
    Inference Adapter can accept partially populated documents and surface
    structured validation errors for genuinely missing fields (e.g., missing
    routing.selected_model or empty request.messages) rather than failing at
    Pydantic parse time.

    Fields the Inference Adapter writes: response, metadata, extensions.
    All other fields are preserved byte-identical in the outbound document.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    timestamp_utc: str | None = None
    user: IMFUser = Field(default_factory=IMFUser)
    request: IMFRequest = Field(default_factory=IMFRequest)
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    routing: IMFRouting = Field(default_factory=IMFRouting)
    cache: IMFCache | None = None
    response: IMFResponse | None = None
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)
