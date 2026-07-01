"""
Pydantic schemas for the Internal Message Format (IMF) as consumed by the Cache Service.

Defines:
  IMFMessage     — a single chat message with role and content
  IMFUsage       — token usage statistics
  IMFResponse    — the LLM response payload (content, finish_reason, usage)
  IMFGovernance  — governance metadata including PII detection results
  IMFRouting     — model routing decisions
  IMFRequest     — the inbound request fields (messages, task_type, model, etc.)
  IMFDocument    — the full IMF envelope the Cache Service accepts on every endpoint
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IMFMessage(BaseModel):
    """A single message in the conversation history."""

    role: str
    content: str


class IMFUsage(BaseModel):
    """Token usage counters returned by the inference layer."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class IMFResponse(BaseModel):
    """The LLM-generated response, stored in and retrieved from the cache."""

    content: str | None = None
    finish_reason: str | None = None
    usage: IMFUsage | None = None


class IMFGovernance(BaseModel):
    """Governance metadata.  Only the PII field list is required for cache operation."""

    pii_fields_detected: list[str] = []
    # Additional governance fields are optional — the cache layer does not act on them
    # but must preserve them across the IMF envelope.
    pii_masked: bool | None = None
    injection_score: float | None = None
    jailbreak_score: float | None = None
    content_safety_passed: bool | None = None
    human_approval_required: bool | None = None
    human_approval_status: str | None = None
    policy_decisions: list | None = None


class IMFRouting(BaseModel):
    """Model routing decisions made by the Intelligent Router."""

    selected_model: str
    routing_mode: str | None = None
    fallback_level: int = 0


class IMFRequest(BaseModel):
    """The consumer's inbound request fields carried inside the IMF envelope."""

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

    request_id: str | None = None
    request: IMFRequest
    routing: IMFRouting
    response: IMFResponse | None = None
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    cache: dict | None = None
