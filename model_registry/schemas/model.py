"""
Pydantic schemas for the Model Registry data models.

Defines:
  ModelStatus          — str enum: active | staging | retired
  TaskType             — str enum: chat | code | reasoning | summarization |
                         translation | vision | embeddings
  ModelRecordCreate    — request body for POST /models (extra="forbid")
  ModelRecord          — full stored record; registered_at always present.
                         Carries `api_key` (plaintext) for cloud-backend
                         models (e.g. backend="anthropic") — the provider
                         credential the Inference Adapter uses to actually
                         call out. Never returned by the public list/get
                         endpoints; see ModelRecordPublic.
  ModelRecordPublic    — response shape for GET /models*: everything in
                         ModelRecord except api_key, plus api_key_set.
  ApiKeyUpdateRequest  — request body for PATCH /models/{name}/api-key
  StatusUpdateRequest  — request body for PATCH /models/{name}/status
  HealthResponse       — response body for GET /health

Security note (POC): api_key is stored in plaintext in models.json, same
trust level as every other shared secret in this repo (env-var plaintext
GATEWAY_API_KEY, etc.). Not production-ready — see CLAUDE.md.
"""

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ModelStatus(str, Enum):
    active  = "active"
    staging = "staging"
    retired = "retired"


class TaskType(str, Enum):
    chat          = "chat"
    code          = "code"
    reasoning     = "reasoning"
    summarization = "summarization"
    translation   = "translation"
    vision        = "vision"
    embeddings    = "embeddings"


NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._:-]+$')  # ':' allowed — Ollama tags are "name:tag"


class ModelRecordCreate(BaseModel):
    """Request body for POST /models. registered_at is auto-populated if absent."""

    model_config = ConfigDict(extra="forbid")  # rejects unknown fields → HTTP 422

    name:               str            = Field(..., pattern=r'^[a-zA-Z0-9._:-]+$')
    version:            str
    backend:            str
    endpoint:           str
    tasks:              list[TaskType] = Field(..., min_length=1)
    status:             ModelStatus
    vram_required_gb:   float | None   = None
    max_context_length: int   | None   = None
    fallback_model:     str   | None   = None
    registered_at:      str   | None   = None  # ISO-8601; auto-set if absent
    notes:              str   | None   = None
    api_key:            str   | None   = None  # provider credential — cloud backends only


class ModelRecord(ModelRecordCreate):
    """Full record as stored and returned internally. registered_at is
    always populated. api_key is present here (used by internal callers,
    e.g. GET /models/{name}/secret) but must never be exposed by a public
    list/get endpoint — use ModelRecordPublic for those."""

    registered_at: str  # overrides Optional above — always present in responses


class ModelRecordPublic(BaseModel):
    """Public response shape: ModelRecord with api_key replaced by a boolean
    flag. Used by GET /models, GET /models/by-task/{type}, GET /models/{name},
    POST /models, and PATCH /models/{name}/status."""

    name:               str
    version:            str
    backend:            str
    endpoint:           str
    tasks:              list[TaskType]
    status:             ModelStatus
    vram_required_gb:   float | None = None
    max_context_length: int   | None = None
    fallback_model:     str   | None = None
    registered_at:      str
    notes:              str   | None = None
    api_key_set:        bool

    @classmethod
    def from_record(cls, record: "ModelRecord") -> "ModelRecordPublic":
        data = record.model_dump(exclude={"api_key"})
        return cls(**data, api_key_set=bool(record.api_key))


class ApiKeyUpdateRequest(BaseModel):
    """Request body for PATCH /models/{name}/api-key."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1)


class StatusUpdateRequest(BaseModel):
    """Request body for PATCH /models/{name}/status."""

    model_config = ConfigDict(extra="forbid")

    status: ModelStatus


class HealthResponse(BaseModel):
    status:  str
    storage: str | None = None
