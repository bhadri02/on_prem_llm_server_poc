"""
Unit tests for IMFMapper.to_anthropic_request / to_imf_response_from_anthropic
/ resolve_anthropic_finish_reason.
"""

from __future__ import annotations

import pytest

from inference_adapter.config import Settings
from inference_adapter.schemas.imf import IMFDocument, IMFMessage, IMFRequest, IMFRouting, IMFUser
from inference_adapter.services.anthropic_client import AnthropicInvalidResponseError
from inference_adapter.services.imf_mapper import IMFMapper


def _make_settings(**overrides) -> Settings:
    defaults = {"default_max_tokens": 2048, "max_tokens_limit": 4096, "default_temperature": 0.7}
    defaults.update(overrides)
    return Settings(**defaults)


def _make_imf(messages: list[IMFMessage] | None = None, **request_overrides) -> IMFDocument:
    if messages is None:
        messages = [IMFMessage(role="user", content="Hello")]
    return IMFDocument(
        request_id="req-001",
        user=IMFUser(user_id="u1", department="eng"),
        request=IMFRequest(task_type="chat", messages=messages, **request_overrides),
        routing=IMFRouting(selected_model="claude-sonnet-5", backend="anthropic"),
    )


# ---------------------------------------------------------------------------
# to_anthropic_request
# ---------------------------------------------------------------------------


def test_system_message_extracted_to_top_level_system():
    imf = _make_imf(messages=[
        IMFMessage(role="system", content="Be concise."),
        IMFMessage(role="user", content="Hi"),
    ])
    payload = IMFMapper.to_anthropic_request(imf, _make_settings())
    assert payload["system"] == "Be concise."
    assert payload["messages"] == [{"role": "user", "content": "Hi"}]


def test_no_system_message_omits_system_key():
    imf = _make_imf(messages=[IMFMessage(role="user", content="Hi")])
    payload = IMFMapper.to_anthropic_request(imf, _make_settings())
    assert "system" not in payload


def test_model_is_selected_model_not_request_model():
    imf = _make_imf()
    payload = IMFMapper.to_anthropic_request(imf, _make_settings())
    assert payload["model"] == "claude-sonnet-5"


def test_max_tokens_defaults_when_absent():
    imf = _make_imf(max_tokens=None)
    payload = IMFMapper.to_anthropic_request(imf, _make_settings(default_max_tokens=999))
    assert payload["max_tokens"] == 999


def test_max_tokens_clamped_to_limit():
    imf = _make_imf(max_tokens=10_000)
    payload = IMFMapper.to_anthropic_request(imf, _make_settings(max_tokens_limit=4096))
    assert payload["max_tokens"] == 4096


def test_temperature_defaults_when_absent():
    imf = _make_imf(temperature=None)
    payload = IMFMapper.to_anthropic_request(imf, _make_settings(default_temperature=0.42))
    assert payload["temperature"] == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# resolve_anthropic_finish_reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("tool_use", None),
        (None, None),
        ("something_unrecognised", None),
    ],
)
def test_resolve_anthropic_finish_reason(stop_reason, expected):
    assert IMFMapper.resolve_anthropic_finish_reason(stop_reason) == expected


# ---------------------------------------------------------------------------
# to_imf_response_from_anthropic
# ---------------------------------------------------------------------------


def test_maps_text_content_and_usage():
    imf_in = _make_imf()
    anthropic_resp = {
        "content": [{"type": "text", "text": "Hello there"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    out = IMFMapper.to_imf_response_from_anthropic(imf_in, anthropic_resp, wall_clock_ms=123)
    assert out.response.content == "Hello there"
    assert out.response.finish_reason == "stop"
    assert out.response.usage.prompt_tokens == 10
    assert out.response.usage.completion_tokens == 5
    assert out.response.usage.total_tokens == 15
    assert out.metadata["inference_backend"] == "anthropic"
    assert out.metadata["inference_latency_ms"] == 123
    assert out.metadata["model_name"] == "claude-sonnet-5"


def test_concatenates_multiple_text_blocks():
    imf_in = _make_imf()
    anthropic_resp = {
        "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    out = IMFMapper.to_imf_response_from_anthropic(imf_in, anthropic_resp, wall_clock_ms=1)
    assert out.response.content == "Hello world"


def test_missing_content_key_raises_invalid_response():
    imf_in = _make_imf()
    with pytest.raises(AnthropicInvalidResponseError):
        IMFMapper.to_imf_response_from_anthropic(imf_in, {"stop_reason": "end_turn"}, wall_clock_ms=1)


def test_empty_content_array_raises_invalid_response():
    imf_in = _make_imf()
    with pytest.raises(AnthropicInvalidResponseError):
        IMFMapper.to_imf_response_from_anthropic(imf_in, {"content": []}, wall_clock_ms=1)


def test_content_with_no_text_block_raises_invalid_response():
    """e.g. a tool_use-only content array — no text for us to surface."""
    imf_in = _make_imf()
    anthropic_resp = {"content": [{"type": "tool_use", "id": "x", "name": "y", "input": {}}]}
    with pytest.raises(AnthropicInvalidResponseError):
        IMFMapper.to_imf_response_from_anthropic(imf_in, anthropic_resp, wall_clock_ms=1)


def test_does_not_mutate_input_document():
    imf_in = _make_imf()
    original_dump = imf_in.model_dump()
    IMFMapper.to_imf_response_from_anthropic(
        imf_in,
        {"content": [{"type": "text", "text": "hi"}], "usage": {}},
        wall_clock_ms=1,
    )
    assert imf_in.model_dump() == original_dump
