"""
Pydantic schemas for the Internal Message Format (IMF) as consumed by the Cache Service.

The leaf blocks (IMFMessage, IMFUsage, IMFResponse, IMFGovernance,
IMFRouting) are re-exported from shared.imf — see that module's docstring
for why those used to be a hand-maintained per-service copy and why that
was risky.

IMFRequest and IMFDocument stay defined here: the Cache Service's contract
is deliberately narrower than the other services' full envelope — it never
needs a `user` block at all (it isn't a document-preserving hop; the
Intelligent Router only reads specific fields back off its response, it
doesn't reconstruct the full IMF from it), and requires task_type/messages
where other services don't. Every class here sets extra="allow" so a field
this file doesn't know about survives this service's parse/dump round trip
unchanged (see shared.imf's docstring for why that matters).

Defines:
  IMFRequest     — the inbound request fields (messages, task_type, model, etc.)
  IMFDocument    — the full IMF envelope the Cache Service accepts on every endpoint
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shared.imf import (  # noqa: F401
    IMFGovernance,
    IMFMessage,
    IMFResponse,
    IMFUsage,
)


class IMFRouting(BaseModel):
    """Model routing decisions made by the Intelligent Router.

    selected_model is required here (no default) — the Cache Service's own
    business logic needs a real cache key input and returns 422 if it's
    missing; every other service's copy of this block makes it optional
    since they only reject its absence conditionally (e.g. inference_adapter
    wants to return a custom-shaped 422 rather than Pydantic's default one).
    """

    model_config = ConfigDict(extra="allow")

    selected_model: str
    routing_mode: str | None = None
    fallback_level: int = 0


class IMFRequest(BaseModel):
    """The consumer's inbound request fields carried inside the IMF envelope."""

    model_config = ConfigDict(extra="allow")

    messages: list[IMFMessage]
    task_type: str
    model: str | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class IMFDocument(BaseModel):
    """
    The full IMF envelope accepted by POST /cache/lookup and POST /cache/write.

    Only the fields the Cache Service reads are strictly required; all other
    standard IMF envelope fields are accepted but not acted upon.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str | None = None
    request: IMFRequest
    routing: IMFRouting
    response: IMFResponse | None = None
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    cache: dict | None = None
