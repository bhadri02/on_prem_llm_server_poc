"""OpenAI-compatible request and response schemas for the API Gateway."""

from pydantic import BaseModel, field_validator


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str | None = None
    messages: list[OpenAIMessage]  # required; Pydantic raises 422 if absent
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None

    @field_validator("messages")
    @classmethod
    def messages_must_be_non_empty(cls, v):
        if not v:
            raise ValueError("messages must be a non-empty array")
        return v


class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[dict]  # [{"id": "...", "object": "model"}]


class OpenAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict]
    usage: dict
