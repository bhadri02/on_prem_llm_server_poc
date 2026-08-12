"""
Unit tests for api_gateway.schemas.openai.OpenAIMessage's content field.

Real OpenAI-compatible clients (e.g. Continue.dev) commonly resend a prior
turn's own content as a multipart content-parts array
(``[{"type": "text", "text": "..."}]``) rather than a plain string, even for
plain text-only chats — not just vision/multimodal input. Before this fix,
OpenAIMessage.content was a strict ``str``, so any such message failed
Pydantic validation with a 400, breaking any multi-turn conversation once a
client resent history in this shape.
"""

import pytest
from pydantic import ValidationError

from api_gateway.schemas.openai import OpenAIChatRequest, OpenAIMessage


def test_plain_string_content_still_accepted():
    msg = OpenAIMessage(role="user", content="hello")
    assert msg.content == "hello"


def test_content_parts_array_is_flattened_to_string():
    msg = OpenAIMessage(
        role="assistant",
        content=[
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ],
    )
    assert msg.content == "part one\npart two"


def test_non_text_parts_are_dropped_not_rejected():
    msg = OpenAIMessage(
        role="user",
        content=[
            {"type": "image_url", "image_url": {"url": "http://example.com/x.png"}},
            {"type": "text", "text": "describe this"},
        ],
    )
    assert msg.content == "describe this"


def test_all_non_text_parts_flattens_to_empty_string():
    msg = OpenAIMessage(
        role="user",
        content=[{"type": "image_url", "image_url": {"url": "http://example.com/x.png"}}],
    )
    assert msg.content == ""


def test_content_parts_array_in_full_chat_request():
    """Reproduces the real Continue.dev failure: a request whose history
    includes an assistant message with array-shaped content must validate."""
    req = OpenAIChatRequest(
        model="llama3.2:3b",
        messages=[
            OpenAIMessage(role="user", content="hi"),
            OpenAIMessage(
                role="assistant",
                content=[{"type": "text", "text": "hello back"}],
            ),
            OpenAIMessage(role="user", content="continue"),
        ],
    )
    assert req.messages[1].content == "hello back"


def test_invalid_content_type_still_rejected():
    """A content value that's neither a string nor a list must still fail."""
    with pytest.raises(ValidationError):
        OpenAIMessage(role="user", content=42)
