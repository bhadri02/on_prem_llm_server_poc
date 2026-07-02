"""
Unit tests for api_gateway/services/serializer.py — serialize_response().

Validates: Requirements 6.1–6.4
"""

from __future__ import annotations

import pytest

from api_gateway.schemas.imf import (
    IMFDocument,
    IMFRequest,
    IMFResponse,
    IMFUsage,
)
from api_gateway.services.serializer import serialize_response


def _make_imf(
    *,
    request_id: str = "test-request-id",
    model: str | None = "gpt-4",
    content: str | None = "Hello!",
    finish_reason: str | None = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
) -> IMFDocument:
    return IMFDocument(
        request_id=request_id,
        trace_id=request_id,
        timestamp_utc="2024-01-01T00:00:00Z",
        request=IMFRequest(model=model),
        response=IMFResponse(
            content=content,
            finish_reason=finish_reason,
            usage=IMFUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# id field  (Req 6.1)
# ---------------------------------------------------------------------------


def test_id_is_prefixed_with_chatcmpl():
    imf = _make_imf(request_id="abc-123")
    result = serialize_response(imf)
    assert result["id"] == "chatcmpl-abc-123"


def test_id_includes_full_request_id():
    rid = "550e8400-e29b-41d4-a716-446655440000"
    imf = _make_imf(request_id=rid)
    result = serialize_response(imf)
    assert result["id"] == f"chatcmpl-{rid}"


# ---------------------------------------------------------------------------
# object field  (Req 6.1)
# ---------------------------------------------------------------------------


def test_object_is_chat_completion():
    result = serialize_response(_make_imf())
    assert result["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# created field  (Req 6.1)
# ---------------------------------------------------------------------------


def test_created_is_an_integer():
    result = serialize_response(_make_imf())
    assert isinstance(result["created"], int)


def test_created_is_positive():
    result = serialize_response(_make_imf())
    assert result["created"] > 0


# ---------------------------------------------------------------------------
# model field  (Req 6.1)
# ---------------------------------------------------------------------------


def test_model_is_mapped_from_imf():
    result = serialize_response(_make_imf(model="llama3"))
    assert result["model"] == "llama3"


def test_model_is_empty_string_when_none():
    result = serialize_response(_make_imf(model=None))
    assert result["model"] == ""


# ---------------------------------------------------------------------------
# choices block  (Req 6.2, 6.3)
# ---------------------------------------------------------------------------


def test_choices_has_exactly_one_entry():
    result = serialize_response(_make_imf())
    assert len(result["choices"]) == 1


def test_choices_index_is_zero():
    result = serialize_response(_make_imf())
    assert result["choices"][0]["index"] == 0


def test_choices_message_role_is_assistant():
    """Req 6.2: role must be 'assistant'."""
    result = serialize_response(_make_imf())
    assert result["choices"][0]["message"]["role"] == "assistant"


def test_choices_message_content_matches_imf():
    """Req 6.2: content must come from imf.response.content."""
    result = serialize_response(_make_imf(content="The answer is 42."))
    assert result["choices"][0]["message"]["content"] == "The answer is 42."


def test_choices_message_content_none_propagates():
    result = serialize_response(_make_imf(content=None))
    assert result["choices"][0]["message"]["content"] is None


def test_choices_finish_reason_matches_imf():
    """Req 6.3: finish_reason must come from imf.response.finish_reason."""
    result = serialize_response(_make_imf(finish_reason="length"))
    assert result["choices"][0]["finish_reason"] == "length"


def test_choices_finish_reason_none_propagates():
    result = serialize_response(_make_imf(finish_reason=None))
    assert result["choices"][0]["finish_reason"] is None


# ---------------------------------------------------------------------------
# usage block  (Req 6.4)
# ---------------------------------------------------------------------------


def test_usage_prompt_tokens_matches_imf():
    result = serialize_response(_make_imf(prompt_tokens=42))
    assert result["usage"]["prompt_tokens"] == 42


def test_usage_completion_tokens_matches_imf():
    result = serialize_response(_make_imf(completion_tokens=17))
    assert result["usage"]["completion_tokens"] == 17


def test_usage_total_tokens_matches_imf():
    result = serialize_response(_make_imf(total_tokens=59))
    assert result["usage"]["total_tokens"] == 59


def test_usage_zero_values_propagate():
    result = serialize_response(
        _make_imf(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    )
    assert result["usage"]["prompt_tokens"] == 0
    assert result["usage"]["completion_tokens"] == 0
    assert result["usage"]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# top-level key presence  (Req 6.1)
# ---------------------------------------------------------------------------


def test_all_required_top_level_keys_present():
    result = serialize_response(_make_imf())
    for key in ("id", "object", "created", "model", "choices", "usage"):
        assert key in result, f"Missing key: {key}"
