from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ModelRecord(BaseModel):
    name: str
    version: str
    backend: str
    tasks: List[str]
    status: Literal["active", "retired", "staging"]


class ModelStatusPatch(BaseModel):
    status: Literal["active", "retired", "staging"]


class ModelRegisterRequest(BaseModel):
    """Body for POST /portal/models — proxied to the Model Registry's
    POST /models unchanged. api_key is required for cloud backends
    (e.g. backend="anthropic") and omitted for on-prem/Ollama models."""

    name: str
    version: str
    backend: str
    endpoint: str
    tasks: List[str]
    status: Literal["active", "retired", "staging"] = "staging"
    vram_required_gb: Optional[float] = None
    max_context_length: Optional[int] = None
    fallback_model: Optional[str] = None
    notes: Optional[str] = None
    api_key: Optional[str] = None


class ModelApiKeyPatch(BaseModel):
    api_key: str


class OllamaSyncRequest(BaseModel):
    """Body for POST /portal/models/sync-ollama. `model` is optional — if
    given, it's pulled via Ollama's own /api/pull first (blocking; can take
    minutes for a large model). Either way, every model Ollama already has
    locally gets registered in the Model Registry if it isn't already."""

    model: Optional[str] = None
    tasks: List[str] = Field(
        default_factory=lambda: ["chat", "code", "reasoning", "summarization", "translation"]
    )


class OllamaSyncResult(BaseModel):
    pulled: Optional[str] = None
    ollama_models: List[str]
    registered: List[str]
    already_registered: List[str]
    failed: dict[str, str] = Field(default_factory=dict)
