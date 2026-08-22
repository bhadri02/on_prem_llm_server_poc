"""
shared/imf.py

Canonical definitions for the *leaf* Internal Message Format (IMF) blocks —
the pieces of the envelope that were genuinely identical (or safely
unifiable) across every service's previously hand-maintained copy:
IMFMessage, IMFUsage, IMFResponse, IMFGovernance, IMFRouting, IMFCache, and
the shared UUID4_RE pattern used to validate request_id.

What's deliberately NOT unified here: IMFUser, the nested "request" block,
and the top-level document class. Those three have genuine, intentional
per-service strictness differences that a single shared class can't
represent — e.g. inference_adapter needs `request.messages` to accept an
explicit empty list (so its own code can return a custom-shaped
`{"event": "empty_messages"}` 422 instead of Pydantic's default error
body), while agent_framework/security_layer/intelligent_router want
Pydantic to reject an empty/missing messages list at parse time. Each
service's own schema module (api_gateway/schemas/imf.py,
cache_service/schemas/imf.py, inference_adapter/schemas/imf.py,
security_layer/models.py, intelligent_router/models.py,
agent_framework/schemas/imf.py) still defines those three locally,
composed from the shared leaf blocks below, preserving its own original
requiredness — see each module's docstring for the exact mapping.

Two things still eliminate the real bug this used to cause (a field
missing from one service's copy getting silently stripped by Pydantic at
that service's parse boundary — confirmed by inspection: every handler
does `imf: dict = body.model_dump()` right after validation, so that's the
only place a field can ever be lost):

1. Every model here, and every per-service IMFUser/request/document class,
   sets `model_config = ConfigDict(extra="allow")`. A field one layer adds
   is preserved verbatim through every other layer's parse/dump round
   trip — including a layer whose own schema doesn't know that field
   exists — so a new field only needs to be added where it's actually
   *read*, not hand-copied into every service just so it survives forwarding.
2. The leaf blocks below are the actual single source of truth for the
   fields that previously drifted for no good reason (e.g. IMFRouting.backend
   existed in some copies but not others). Per-service IMFUser classes are
   no longer independently missing fields either — each one now includes
   key_id/model_entitlements/rate_limit_override, even where a service
   doesn't act on them itself, so they survive that service's hop.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class IMFMessage(BaseModel):
    """A single message in the conversation history."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str


class IMFUsage(BaseModel):
    """Token usage counters."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IMFResponse(BaseModel):
    """The LLM-generated response block."""

    model_config = ConfigDict(extra="allow")

    content: str | None = None
    finish_reason: str | None = None
    usage: IMFUsage | None = Field(default_factory=IMFUsage)


class IMFGovernance(BaseModel):
    """Governance/security metadata populated by the Security Layer.

    policy_decisions is untyped-item (list[Any]) rather than list[str] —
    intelligent_router appends dict entries to this list for its own
    policy decisions (its own prior schema's comment noted this
    explicitly: "str from security layer, dict in prod"), so a stricter
    item type would reject documents that already round-trip fine today.
    """

    model_config = ConfigDict(extra="allow")

    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list[Any] = Field(default_factory=list)


class IMFRouting(BaseModel):
    """Model routing decisions made by the Intelligent Router.

    backend is stamped by the Router from model_matrix.yaml's
    ModelEntry.backend — it tells the Inference Adapter which client to
    dispatch through ("ollama" vs a cloud provider) without an extra
    per-request Model Registry lookup for the common (Ollama) case.
    Absent/None is treated as "ollama" for backward compatibility.
    """

    model_config = ConfigDict(extra="allow")

    selected_model: str | None = None
    routing_mode: str = "auto"
    fallback_level: int = 0
    backend: str | None = None


class IMFCache(BaseModel):
    """Cache lookup/write state."""

    model_config = ConfigDict(extra="allow")

    lookup_hit: bool = False
    cache_key: str | None = None
