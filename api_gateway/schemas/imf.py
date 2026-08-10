"""IMF (Internal Message Format) Pydantic models for the API Gateway."""

from pydantic import BaseModel, Field


class IMFMessage(BaseModel):
    role: str       # "system" | "user" | "assistant"
    content: str


class IMFUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IMFResponse(BaseModel):
    content: str | None = None
    finish_reason: str | None = None   # "stop" | "length" | "tool_call" | None
    usage: IMFUsage = Field(default_factory=IMFUsage)


class IMFGovernance(BaseModel):
    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list = Field(default_factory=list)


class IMFRouting(BaseModel):
    selected_model: str | None = None
    routing_mode: str = "auto"
    fallback_level: int = 0


class IMFCache(BaseModel):
    lookup_hit: bool = False
    cache_key: str | None = None


class IMFUser(BaseModel):
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
    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = Field(default_factory=list)
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7


class IMFDocument(BaseModel):
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
