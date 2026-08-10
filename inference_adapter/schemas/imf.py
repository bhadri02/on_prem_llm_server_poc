"""
Pydantic schemas for the Internal Message Format (IMF) as consumed by the Inference Adapter.

Defines:
  IMFMessage     — a single chat message with role and content
  IMFUsage       — token usage statistics
  IMFResponse    — the LLM response payload (content, finish_reason, usage)
  IMFGovernance  — governance metadata including PII detection results and policy decisions
  IMFRouting     — model routing decisions (selected_model may be absent on inbound requests)
  IMFUser        — caller identity and department for Prometheus labels and PII handling
  IMFRequest     — the inbound request fields (messages, task_type, model, stream, etc.)
  IMFCache       — cache lookup state carried in the envelope
  IMFDocument    — the full IMF envelope accepted on POST /infer

All fields outside `response`, `metadata`, and `extensions` are preserved unchanged
when the Inference Adapter returns the outbound IMF document.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IMFMessage(BaseModel):
    """A single message in the conversation history."""

    role: str
    content: str


class IMFUsage(BaseModel):
    """Token usage counters populated from the Ollama response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IMFResponse(BaseModel):
    """The LLM-generated response block, populated by the Inference Adapter."""

    content: str | None = None
    finish_reason: str | None = None
    usage: IMFUsage | None = None


class IMFGovernance(BaseModel):
    """
    Governance metadata.

    pii_fields_detected is the only field the Inference Adapter actively reads —
    it drives the log PII-exclusion logic.  All other fields are optional and
    are preserved unchanged in the outbound IMF envelope.
    """

    pii_fields_detected: list[str] = []
    pii_masked: bool | None = None
    injection_score: float | None = None
    jailbreak_score: float | None = None
    content_safety_passed: bool | None = None
    human_approval_required: bool | None = None
    human_approval_status: str | None = None
    policy_decisions: list | None = None


class IMFRouting(BaseModel):
    """
    Model routing decisions made by the Intelligent Router.

    selected_model is optional here so that the Inference Adapter can detect
    and reject requests where it is absent (Requirement 1.7).

    backend is stamped by the Router from model_matrix.yaml's ModelEntry.backend
    (see intelligent_router/pipeline.py Stage 3) — it tells the Inference
    Adapter which client to dispatch through ("ollama" vs a cloud provider
    like "anthropic") without a per-request Model Registry lookup for the
    common (Ollama) case. Absent/None is treated as "ollama" for backward
    compatibility with callers that don't set it.
    """

    selected_model: str | None = None
    routing_mode: str | None = None
    fallback_level: int = 0
    backend: str | None = None


class IMFUser(BaseModel):
    """Caller identity carried in the IMF envelope."""

    user_id: str | None = None
    department: str | None = None
    roles: list[str] = []
    auth_method: str | None = None


class IMFRequest(BaseModel):
    """
    The consumer's inbound request fields carried inside the IMF envelope.

    All fields are optional with sensible defaults so that the Inference Adapter
    can provide structured 422 errors for missing required fields rather than
    letting Pydantic raise a validation error at parse time.
    """

    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = []
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class IMFCache(BaseModel):
    """Cache lookup state carried through the IMF envelope."""

    lookup_hit: bool = False
    cache_key: str | None = None


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
