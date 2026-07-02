from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str = Field(..., description="Model name; must be non-empty")
    messages: List[Message] = Field(..., min_length=1, description="Conversation messages; at least one required")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature (0.0–2.0)")

    @field_validator("model")
    @classmethod
    def model_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model must not be empty or whitespace")
        return v


class ChatResponse(BaseModel):
    """OpenAI-compatible chat completion response.

    Core fields are declared explicitly; any additional fields from the
    upstream API Gateway are passed through via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    request_id: Optional[str] = None
    id: Optional[str] = None
    object: Optional[str] = None
    created: Optional[int] = None
    model: Optional[str] = None
