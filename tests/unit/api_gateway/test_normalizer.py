"""
Unit tests for api_gateway/services/normalizer.py — build_imf().

Validates: Requirements 4.1–4.12, 11.1–11.5
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

from api_gateway.schemas.imf import IMFDocument
from api_gateway.schemas.openai import OpenAIChatRequest, OpenAIMessage
from api_gateway.services.normalizer import build_imf

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _req(**kwargs) -> OpenAIChatRequest:
    """Helper to build a minimal valid OpenAIChatRequest."""
    defaults = {"messages": [OpenAIMessage(role="user", content="hello")]}
    defaults.update(kwargs)
    return OpenAIChatRequest(**defaults)


# ---------------------------------------------------------------------------
# request_id / trace_id / span_id  (Req 4.1, 4.2, 11.1, 11.2, 11.3)
# ---------------------------------------------------------------------------


def test_request_id_is_uuid_v4():
    """Req 4.1: request_id must be a UUID v4."""
    imf = build_imf(_req())
    assert UUID_V4_RE.match(imf.request_id), f"Not a UUID v4: {imf.request_id}"


def test_trace_id_equals_request_id():
    """Req 4.2: trace_id must equal request_id for POC."""
    imf = build_imf(_req())
    assert imf.trace_id == imf.request_id


def test_span_id_is_empty_string():
    """Req 11.2: span_id is empty string for POC (OTel deferred)."""
    imf = build_imf(_req())
    assert imf.span_id == ""


def test_each_call_generates_unique_request_id():
    """Req 4.1: each invocation produces a fresh UUID."""
    ids = {build_imf(_req()).request_id for _ in range(10)}
    assert len(ids) == 10


# ---------------------------------------------------------------------------
# timestamp_utc  (Req 4.3, 11.1)
# ---------------------------------------------------------------------------


def test_timestamp_utc_is_iso8601_with_z():
    """Req 4.3: timestamp_utc must be parseable ISO-8601 UTC."""
    imf = build_imf(_req())
    ts = imf.timestamp_utc
    # Must end with Z (the format used by the normalizer)
    assert ts.endswith("Z"), f"Expected Z suffix, got: {ts}"
    # Must be parseable
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# user block  (Req 4.4, 4.5)
# ---------------------------------------------------------------------------


def test_user_id_is_poc_user():
    imf = build_imf(_req())
    assert imf.user.user_id == "poc-user"


def test_user_department_is_poc():
    imf = build_imf(_req())
    assert imf.user.department == "poc"


def test_user_roles_is_developer_list():
    imf = build_imf(_req())
    assert imf.user.roles == ["developer"]


def test_user_auth_method_is_api_key():
    imf = build_imf(_req())
    assert imf.user.auth_method == "api_key"


# ---------------------------------------------------------------------------
# request.model  (Req 4.6)
# ---------------------------------------------------------------------------


def test_model_is_mapped_when_provided():
    imf = build_imf(_req(model="gpt-4"))
    assert imf.request.model == "gpt-4"


def test_model_is_none_when_absent():
    imf = build_imf(_req())  # model not provided
    assert imf.request.model is None


# ---------------------------------------------------------------------------
# request.messages  (Req 4.7, 11.5)
# ---------------------------------------------------------------------------


def test_messages_are_preserved_in_order():
    msgs = [
        OpenAIMessage(role="system", content="You are helpful."),
        OpenAIMessage(role="user", content="What is 2+2?"),
    ]
    imf = build_imf(_req(messages=msgs))
    assert len(imf.request.messages) == 2
    assert imf.request.messages[0].role == "system"
    assert imf.request.messages[0].content == "You are helpful."
    assert imf.request.messages[1].role == "user"
    assert imf.request.messages[1].content == "What is 2+2?"


def test_single_message_preserved():
    msg = OpenAIMessage(role="user", content="hello world")
    imf = build_imf(_req(messages=[msg]))
    assert len(imf.request.messages) == 1
    assert imf.request.messages[0].role == "user"
    assert imf.request.messages[0].content == "hello world"


# ---------------------------------------------------------------------------
# request.stream  (Req 4.8)
# ---------------------------------------------------------------------------


def test_stream_defaults_to_false():
    imf = build_imf(_req())  # stream not provided
    assert imf.request.stream is False


def test_stream_true_is_mapped():
    imf = build_imf(_req(stream=True))
    assert imf.request.stream is True


def test_stream_false_is_mapped():
    imf = build_imf(_req(stream=False))
    assert imf.request.stream is False


# ---------------------------------------------------------------------------
# request.max_tokens  (Req 4.9)
# ---------------------------------------------------------------------------


def test_max_tokens_defaults_to_2048():
    imf = build_imf(_req())  # max_tokens not provided
    assert imf.request.max_tokens == 2048


def test_max_tokens_is_mapped_when_provided():
    imf = build_imf(_req(max_tokens=512))
    assert imf.request.max_tokens == 512


# ---------------------------------------------------------------------------
# request.temperature  (Req 4.10)
# ---------------------------------------------------------------------------


def test_temperature_defaults_to_0_7():
    imf = build_imf(_req())  # temperature not provided
    assert imf.request.temperature == pytest.approx(0.7)


def test_temperature_is_mapped_when_provided():
    imf = build_imf(_req(temperature=0.0))
    assert imf.request.temperature == pytest.approx(0.0)


def test_temperature_1_0_is_mapped():
    imf = build_imf(_req(temperature=1.0))
    assert imf.request.temperature == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# governance / routing / cache / response defaults  (Req 4.11)
# ---------------------------------------------------------------------------


def test_governance_block_has_schema_defaults():
    imf = build_imf(_req())
    g = imf.governance
    assert g.pii_masked is False
    assert g.pii_fields_detected == []
    assert g.injection_score == pytest.approx(0.0)
    assert g.jailbreak_score == pytest.approx(0.0)
    assert g.content_safety_passed is True
    assert g.human_approval_required is False
    assert g.human_approval_status == "not_required"
    assert g.policy_decisions == []


def test_routing_block_has_schema_defaults():
    imf = build_imf(_req())
    r = imf.routing
    assert r.selected_model is None
    assert r.routing_mode == "auto"
    assert r.fallback_level == 0


def test_cache_block_has_schema_defaults():
    imf = build_imf(_req())
    c = imf.cache
    assert c.lookup_hit is False
    assert c.cache_key is None


def test_response_block_has_schema_defaults():
    imf = build_imf(_req())
    r = imf.response
    assert r.content is None
    assert r.finish_reason is None
    assert r.usage.prompt_tokens == 0
    assert r.usage.completion_tokens == 0
    assert r.usage.total_tokens == 0


def test_metadata_is_empty_dict():
    imf = build_imf(_req())
    assert imf.metadata == {}


def test_extensions_is_empty_dict():
    imf = build_imf(_req())
    assert imf.extensions == {}


# ---------------------------------------------------------------------------
# IMF round-trip  (Req 11.6)
# ---------------------------------------------------------------------------


def test_imf_round_trip_preserves_all_fields():
    """Req 11.6: serialize → deserialize must preserve all field values."""
    payload = _req(
        model="llama3",
        messages=[
            OpenAIMessage(role="system", content="Be concise."),
            OpenAIMessage(role="user", content="Explain gravity."),
        ],
        stream=True,
        max_tokens=256,
        temperature=0.3,
    )
    original = build_imf(payload)
    round_tripped = IMFDocument.model_validate(original.model_dump())

    assert round_tripped.request_id == original.request_id
    assert round_tripped.trace_id == original.trace_id
    assert round_tripped.span_id == original.span_id
    assert round_tripped.timestamp_utc == original.timestamp_utc
    assert round_tripped.user.model_dump() == original.user.model_dump()
    assert round_tripped.request.model_dump() == original.request.model_dump()
    assert round_tripped.governance.model_dump() == original.governance.model_dump()
    assert round_tripped.routing.model_dump() == original.routing.model_dump()
    assert round_tripped.cache.model_dump() == original.cache.model_dump()
    assert round_tripped.response.model_dump() == original.response.model_dump()
    assert round_tripped.metadata == original.metadata
    assert round_tripped.extensions == original.extensions
