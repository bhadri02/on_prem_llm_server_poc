"""
Pydantic schemas for the Model Registry data models.

Defines:
  ModelStatus          — str enum: active | staging | retired
  TaskType             — str enum: chat | code | reasoning | summarization |
                         translation | vision | embeddings
  ModelRecordCreate    — request body for POST /models (extra="forbid")
  ModelRecord          — full stored record; registered_at always present
  StatusUpdateRequest  — request body for PATCH /models/{name}/status
  HealthResponse       — response body for GET /health
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


NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')


class ModelRecordCreate(BaseModel):
    """Request body for POST /models. registered_at is auto-populated if absent."""

    model_config = ConfigDict(extra="forbid")  # rejects unknown fields → HTTP 422

    name:               str            = Field(..., pattern=r'^[a-zA-Z0-9._-]+$')
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


class ModelRecord(ModelRecordCreate):
    """Full record as stored and returned. registered_at is always populated."""

    registered_at: str  # overrides Optional above — always present in responses


class StatusUpdateRequest(BaseModel):
    """Request body for PATCH /models/{name}/status."""

    model_config = ConfigDict(extra="forbid")

    status: ModelStatus


class HealthResponse(BaseModel):
    status:  str
    storage: str | None = None
