"""
Unit tests for inference_adapter.services.imf_mapper.IMFMapper.

All tests are synchronous — IMFMapper has no async methods.
IMFDocument instances are built directly from the Pydantic models.
"""

from __future__ import annotations

import pytest

from inference_adapter.config import Settings
from inference_adapter.schemas.imf import (
    IMFDocument,
    IMFGovernance,
    IMFMessage,
    IMFRequest,
    IMFRouting,
    IMFUser,
)
from inference_adapter.services.imf_mapper import IMFMapper
from inference_adapter.services.ollama_client import OllamaInvalidResponseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Build a Settings instance with optional field overrides."""
    defaults = {
        "default_max_tokens": 2048,
        "max_tokens_limit": 4096,
        "default_temperature": 0.7,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_imf(
    selected_model: str = "llama3.2:3b",
    messages: list[IMFMessage] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    stream: bool = False,
) -> IMFDocument:
    """Build a minimal IMFDocument for mapper tests."""
    if messages is None:
        messages = [IMFMessage(role="user", content="Hello")]
    return IMFDocument(
        request_id="req-001",
        user=IMFUser(user_id="u1", department="eng"),
        request=IMFRequest(
            model="request-model",  # should NOT be used by to_ollama_request
            task_type="chat",
            messages=messages,
            stream=stream,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        routing=IMFRouting(selected_model=selected_model),
    )


# ---------------------------------------------------------------------------
# to_ollama_request — model source
# ---------------------------------------------------------------------------

def test_to_ollama_request_model_from_routing_not_request():
    """Model name must come from routing.selected_model, not request.model."""
    imf = _make_imf(selected_model="llama3.2:3b")
    settings = _make_settings()
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["model"] == "llama3.2:3b"


# ---------------------------------------------------------------------------
# to_ollama_request — max_tokens / num_predict logic
# ---------------------------------------------------------------------------

def test_to_ollama_request_null_max_tokens_uses_default():
    """None max_tokens falls back to settings.default_max_tokens."""
    imf = _make_imf(max_tokens=None)
    settings = _make_settings(default_max_tokens=2048)
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["num_predict"] == 2048


def test_to_ollama_request_zero_max_tokens_uses_default():
    """Zero max_tokens (falsy) falls back to settings.default_max_tokens."""
    imf = _make_imf(max_tokens=0)
    settings = _make_settings(default_max_tokens=2048)
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["num_predict"] == 2048


def test_to_ollama_request_valid_max_tokens_passthrough():
    """A valid positive max_tokens within the limit is passed through unchanged."""
    imf = _make_imf(max_tokens=512)
    settings = _make_settings(max_tokens_limit=4096)
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["num_predict"] == 512


def test_to_ollama_request_max_tokens_clamped_at_limit(capsys):
    """max_tokens exceeding max_tokens_limit is clamped to max_tokens_limit."""
    imf = _make_imf(max_tokens=9999)
    settings = _make_settings(max_tokens_limit=4096)
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["num_predict"] == 4096
    # Verify the warning was written to stdout
    captured = capsys.readouterr()
    assert "max_tokens_clamped" in captured.out


# ---------------------------------------------------------------------------
# to_ollama_request — temperature logic
# ---------------------------------------------------------------------------

def test_to_ollama_request_null_temperature_uses_default():
    """None temperature falls back to settings.default_temperature."""
    imf = _make_imf(temperature=None)
    settings = _make_settings(default_temperature=0.7)
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["temperature"] == 0.7


def test_to_ollama_request_temperature_passthrough():
    """An explicit temperature value is passed through unchanged."""
    imf = _make_imf(temperature=1.2)
    settings = _make_settings()
    result = IMFMapper.to_ollama_request(imf, settings)
    assert result["options"]["temperature"] == 1.2


# ---------------------------------------------------------------------------
# to_ollama_request — stream always False
# ---------------------------------------------------------------------------

def test_to_ollama_request_stream_always_false():
    """stream must always be False in the Ollama payload."""
    for stream_input in (True, False):
        imf = _make_imf(stream=stream_input)
        result = IMFMapper.to_ollama_request(imf, _make_settings())
        assert result["stream"] is False


# ---------------------------------------------------------------------------
# to_ollama_request — exactly four keys
# ---------------------------------------------------------------------------

def test_to_ollama_request_only_four_keys():
    """The Ollama request payload must contain exactly the four expected keys."""
    imf = _make_imf()
    result = IMFMapper.to_ollama_request(imf, _make_settings())
    assert set(result.keys()) == {"model", "messages", "stream", "options"}


# ---------------------------------------------------------------------------
# to_imf_response — content mapping
# ---------------------------------------------------------------------------

def _make_ollama_resp(**overrides) -> dict:
    """Build a minimal valid Ollama response dict."""
    base = {
        "message": {"role": "assistant", "content": "Hello, world!"},
        "done_reason": "stop",
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 5,
        "total_duration": 1_500_000_000,
    }
    base.update(overrides)
    return base


def test_to_imf_response_content_mapped_correctly():
    """response.content is mapped from ollama_resp['message']['content']."""
    imf = _make_imf()
    resp = _make_ollama_resp()
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=200)
    assert result.response is not None
    assert result.response.content == "Hello, world!"


def test_to_imf_response_finish_reason_stop():
    """done_reason='stop' maps to finish_reason='stop'."""
    imf = _make_imf()
    resp = _make_ollama_resp(done_reason="stop")
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=200)
    assert result.response.finish_reason == "stop"


def test_to_imf_response_finish_reason_length():
    """done_reason='length' maps to finish_reason='length'."""
    imf = _make_imf()
    resp = _make_ollama_resp(done_reason="length")
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=200)
    assert result.response.finish_reason == "length"


def test_to_imf_response_finish_reason_other_maps_null():
    """Any unrecognised done_reason maps to finish_reason=None."""
    imf = _make_imf()
    resp = _make_ollama_resp(done_reason="some_other_reason")
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=200)
    assert result.response.finish_reason is None


# ---------------------------------------------------------------------------
# to_imf_response — validation errors
# ---------------------------------------------------------------------------

def test_to_imf_response_missing_message_raises_invalid_response_error():
    """Missing 'message' key in Ollama response raises OllamaInvalidResponseError."""
    imf = _make_imf()
    bad_resp = {"done": True, "done_reason": "stop"}  # no 'message' key
    with pytest.raises(OllamaInvalidResponseError):
        IMFMapper.to_imf_response(imf, bad_resp, wall_clock_ms=200)


def test_to_imf_response_missing_content_raises_invalid_response_error():
    """Missing 'content' inside message raises OllamaInvalidResponseError."""
    imf = _make_imf()
    bad_resp = {"message": {"role": "assistant"}, "done": True}  # no 'content'
    with pytest.raises(OllamaInvalidResponseError):
        IMFMapper.to_imf_response(imf, bad_resp, wall_clock_ms=200)


# ---------------------------------------------------------------------------
# to_imf_response — latency calculation
# ---------------------------------------------------------------------------

def test_to_imf_response_total_duration_converts_to_ms():
    """total_duration=1_500_000_000 ns → inference_latency_ms == 1500."""
    imf = _make_imf()
    resp = _make_ollama_resp(total_duration=1_500_000_000)
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=9999)
    assert result.metadata["inference_latency_ms"] == 1500


def test_to_imf_response_zero_total_duration_uses_wall_clock():
    """total_duration <= 0 falls back to wall_clock_ms."""
    imf = _make_imf()
    resp = _make_ollama_resp(total_duration=0)
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=250)
    assert result.metadata["inference_latency_ms"] == 250


def test_to_imf_response_missing_total_duration_uses_wall_clock():
    """Absent total_duration also falls back to wall_clock_ms."""
    imf = _make_imf()
    resp = {"message": {"role": "assistant", "content": "hi"}, "done": True}
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=300)
    assert result.metadata["inference_latency_ms"] == 300


# ---------------------------------------------------------------------------
# to_imf_response — field preservation
# ---------------------------------------------------------------------------

def test_to_imf_response_preserves_input_fields_unchanged():
    """All fields other than response/metadata/extensions are unchanged."""
    imf = _make_imf(selected_model="llama3.2:3b")
    resp = _make_ollama_resp()
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=100)

    assert result.request_id == imf.request_id
    assert result.routing.selected_model == "llama3.2:3b"
    assert result.user.user_id == imf.user.user_id
    assert result.request.task_type == imf.request.task_type


def test_to_imf_response_metadata_backend_and_model():
    """metadata contains inference_backend='ollama' and correct model_name."""
    imf = _make_imf(selected_model="llama3.2:3b")
    resp = _make_ollama_resp()
    result = IMFMapper.to_imf_response(imf, resp, wall_clock_ms=100)
    assert result.metadata["inference_backend"] == "ollama"
    assert result.metadata["model_name"] == "llama3.2:3b"


# ---------------------------------------------------------------------------
# resolve_finish_reason
# ---------------------------------------------------------------------------

def test_resolve_finish_reason_stop():
    assert IMFMapper.resolve_finish_reason("stop") == "stop"


def test_resolve_finish_reason_length():
    assert IMFMapper.resolve_finish_reason("length") == "length"


def test_resolve_finish_reason_other_maps_null():
    assert IMFMapper.resolve_finish_reason("cancelled") is None
    assert IMFMapper.resolve_finish_reason(None) is None
    assert IMFMapper.resolve_finish_reason("") is None


# ---------------------------------------------------------------------------
# resolve_token_counts
# ---------------------------------------------------------------------------

def test_resolve_token_counts_nulls_default_to_zero():
    """None values for both args produce (0, 0, 0)."""
    prompt, completion, total = IMFMapper.resolve_token_counts(None, None)
    assert prompt == 0
    assert completion == 0
    assert total == 0


def test_resolve_token_counts_total_equals_sum():
    """total_tokens == prompt_tokens + completion_tokens."""
    prompt, completion, total = IMFMapper.resolve_token_counts(10, 5)
    assert prompt == 10
    assert completion == 5
    assert total == 15


def test_resolve_token_counts_partial_null():
    """One None arg is treated as 0 while the other is preserved."""
    prompt, completion, total = IMFMapper.resolve_token_counts(7, None)
    assert prompt == 7
    assert completion == 0
    assert total == 7
