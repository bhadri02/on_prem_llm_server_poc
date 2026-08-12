"""OpenAI-compatible request and response schemas for the API Gateway."""

from typing import Any

from pydantic import BaseModel, field_validator


class OpenAIMessage(BaseModel):
    role: str
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def flatten_content_parts(cls, v: Any) -> Any:
        """Accept OpenAI's multipart content-parts array, not just a plain string.

        Real OpenAI-compatible clients (e.g. Continue.dev) commonly resend a
        prior turn's own content as ``[{"type": "text", "text": "..."}, ...]``
        once conversation history builds up, even for plain text-only chats —
        not just for vision/multimodal input. This pipeline has no image
        support, so non-text parts (e.g. ``image_url``) are dropped rather
        than rejected; a purely-image message flattens to an empty string.
        """
        if isinstance(v, list):
            return "\n".join(
                part.get("text", "")
                for part in v
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return v


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
