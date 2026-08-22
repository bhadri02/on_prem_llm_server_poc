"""IMF (Internal Message Format) Pydantic models for the API Gateway.

The leaf blocks (IMFMessage, IMFUsage, IMFResponse, IMFGovernance,
IMFRouting, IMFCache) are re-exported from shared.imf — see that module's
docstring for why those used to be a hand-maintained per-service copy and
why that was risky.

IMFUser/IMFRequest/IMFDocument stay defined here rather than importing a
shared composite: api_gateway is the one service that *constructs* these
(from a resolved API-key profile / client OpenAI-style payload) rather than
parsing an already-built envelope, so its own requiredness/defaults don't
need to match any other service's parse-boundary strictness. Every class
here sets extra="allow" so a field this file doesn't know about survives
this service's parse/dump round trip unchanged (see shared.imf's docstring
for why that matters).
"""

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
    model_config = ConfigDict(extra="allow")

    user_id: str = "poc-user"
    department: str = "poc"
    roles: list[str] = Field(default_factory=lambda: ["developer"])
    auth_method: str = "api_key"
    # Populated server-side from the resolved API key profile (Phase 2 —
    # RBAC + per-user API keys). Optional/empty-safe so older callers that
    # don't set them keep working unchanged.
    key_id: str | None = None
    model_entitlements: list[str] = Field(default_factory=list)
    rate_limit_override: int | None = None


class IMFRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = Field(default_factory=list)
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7


class IMFDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str
    trace_id: str
    span_id: str = ""
    timestamp_utc: str
    user: IMFUser = Field(default_factory=IMFUser)
    request: IMFRequest = Field(default_factory=IMFRequest)
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    routing: IMFRouting = Field(default_factory=IMFRouting)
    cache: IMFCache = Field(default_factory=IMFCache)
    response: IMFResponse = Field(default_factory=IMFResponse)
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)
