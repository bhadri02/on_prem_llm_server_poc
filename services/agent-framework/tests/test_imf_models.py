"""
tests/test_imf_models.py

Unit tests for the IMF Pydantic v2 models (Task 2.1).

Coverage:
  - Valid IMF document parses correctly
  - request.messages empty array raises ValidationError (Req 1.3)
  - request.messages missing raises ValidationError (Req 1.3)
  - request_id non-UUID v4 raises ValidationError (Req 1.4, 10.1)
  - Valid UUID v4 variants are accepted
  - extensions defaults to {} and is safely accessible (Req 10.1)
  - extensions.agentic flag gate works (Req 1.3)
  - All IMF sub-models have correct defaults
  - model_dump() round-trips cleanly
  - extra fields on IMFDocument are allowed (Req 10.3, 10.4)

Requirements: 1.1, 1.3, 1.4, 10.1
"""

import pytest
from pydantic import ValidationError

from agent_framework.schemas.imf import (
    IMFCache,
    IMFDocument,
    IMFGovernance,
    IMFMessage,
    IMFRequest,
    IMFResponse,
    IMFRouting,
    IMFUsage,
    IMFUser,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UUID_V4 = "550e8400-e29b-41d4-a716-446655440000"
ONE_MESSAGE = [{"role": "user", "content": "Hello, agent!"}]


def _minimal_doc(**overrides) -> dict:
    """Return a minimal valid IMFDocument payload dict."""
    base = {
        "request_id": VALID_UUID_V4,
        "user": {"user_id": "test-user"},
        "request": {"messages": ONE_MESSAGE},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# IMFMessage
# ---------------------------------------------------------------------------


class TestIMFMessage:
    def test_valid_message(self):
        msg = IMFMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_role(self):
        msg = IMFMessage(role="assistant", content="Hi there")
        assert msg.role == "assistant"

    def test_system_role(self):
        msg = IMFMessage(role="system", content="You are helpful.")
        assert msg.role == "system"

    def test_missing_role_raises(self):
        with pytest.raises(ValidationError):
            IMFMessage(content="no role")

    def test_missing_content_raises(self):
        with pytest.raises(ValidationError):
            IMFMessage(role="user")


# ---------------------------------------------------------------------------
# IMFUsage
# ---------------------------------------------------------------------------


class TestIMFUsage:
    def test_defaults_are_zero(self):
        usage = IMFUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_custom_values(self):
        usage = IMFUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert usage.total_tokens == 30


# ---------------------------------------------------------------------------
# IMFResponse
# ---------------------------------------------------------------------------


class TestIMFResponse:
    def test_defaults(self):
        resp = IMFResponse()
        assert resp.content is None
        assert resp.finish_reason is None
        assert isinstance(resp.usage, IMFUsage)
        assert resp.usage.total_tokens == 0

    def test_populated(self):
        resp = IMFResponse(content="answer", finish_reason="stop")
        assert resp.content == "answer"
        assert resp.finish_reason == "stop"


# ---------------------------------------------------------------------------
# IMFGovernance
# ---------------------------------------------------------------------------


class TestIMFGovernance:
    def test_defaults(self):
        gov = IMFGovernance()
        assert gov.pii_masked is False
        assert gov.pii_fields_detected == []
        assert gov.injection_score == 0.0
        assert gov.jailbreak_score == 0.0
        assert gov.content_safety_passed is True
        assert gov.human_approval_required is False
        assert gov.human_approval_status == "not_required"
        assert gov.policy_decisions == []


# ---------------------------------------------------------------------------
# IMFRouting
# ---------------------------------------------------------------------------


class TestIMFRouting:
    def test_defaults(self):
        routing = IMFRouting()
        assert routing.selected_model is None
        assert routing.routing_mode == "auto"
        assert routing.fallback_level == 0

    def test_custom_routing_mode(self):
        routing = IMFRouting(routing_mode="pinned", selected_model="llama3.2:3b")
        assert routing.routing_mode == "pinned"
        assert routing.selected_model == "llama3.2:3b"


# ---------------------------------------------------------------------------
# IMFCache
# ---------------------------------------------------------------------------


class TestIMFCache:
    def test_defaults(self):
        cache = IMFCache()
        assert cache.lookup_hit is False
        assert cache.cache_key is None

    def test_hit(self):
        cache = IMFCache(lookup_hit=True, cache_key="abc123")
        assert cache.lookup_hit is True
        assert cache.cache_key == "abc123"


# ---------------------------------------------------------------------------
# IMFUser
# ---------------------------------------------------------------------------


class TestIMFUser:
    def test_required_user_id(self):
        user = IMFUser(user_id="alice")
        assert user.user_id == "alice"

    def test_defaults(self):
        user = IMFUser(user_id="bob")
        assert user.department == ""
        assert user.roles == []
        assert user.auth_method == "api_key"

    def test_missing_user_id_raises(self):
        with pytest.raises(ValidationError):
            IMFUser(department="eng")

    def test_full_user(self):
        user = IMFUser(
            user_id="carol",
            department="engineering",
            roles=["admin", "developer"],
            auth_method="oidc",
        )
        assert user.roles == ["admin", "developer"]
        assert user.auth_method == "oidc"


# ---------------------------------------------------------------------------
# IMFRequest
# ---------------------------------------------------------------------------


class TestIMFRequest:
    def test_valid_request(self):
        req = IMFRequest(messages=[IMFMessage(role="user", content="hi")])
        assert len(req.messages) == 1
        assert req.stream is False
        assert req.max_tokens == 2048
        assert req.temperature == 0.7

    def test_empty_messages_raises(self):
        """Req 1.3: empty messages array must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest(messages=[])
        errors = exc_info.value.errors()
        assert any("messages" in str(e["loc"]) for e in errors)

    def test_missing_messages_raises(self):
        """Req 1.3: missing messages field must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest()
        errors = exc_info.value.errors()
        assert any("messages" in str(e["loc"]) for e in errors)

    def test_multiple_messages(self):
        messages = [
            IMFMessage(role="system", content="Be helpful."),
            IMFMessage(role="user", content="What is 2+2?"),
        ]
        req = IMFRequest(messages=messages)
        assert len(req.messages) == 2

    def test_optional_fields_accept_none(self):
        req = IMFRequest(
            messages=[IMFMessage(role="user", content="hi")],
            model=None,
            task_type=None,
        )
        assert req.model is None
        assert req.task_type is None


# ---------------------------------------------------------------------------
# IMFDocument — UUID v4 validation (Req 1.4, 10.1)
# ---------------------------------------------------------------------------


class TestIMFDocumentUUIDValidation:
    def test_valid_uuid_v4_accepted(self):
        doc = IMFDocument(**_minimal_doc())
        assert doc.request_id == VALID_UUID_V4

    def test_another_valid_uuid_v4(self):
        doc = IMFDocument(
            **_minimal_doc(request_id="f47ac10b-58cc-4372-a567-0e02b2c3d479")
        )
        assert doc.request_id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_non_uuid_raises(self):
        """Req 1.4, 10.1: non-UUID request_id must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IMFDocument(**_minimal_doc(request_id="not-a-uuid"))
        errors = exc_info.value.errors()
        assert any("request_id" in str(e["loc"]) for e in errors)

    def test_uuid_v1_rejected(self):
        """UUID v1 must be rejected (version digit is not 4)."""
        uuid_v1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        with pytest.raises(ValidationError):
            IMFDocument(**_minimal_doc(request_id=uuid_v1))

    def test_uuid_v3_rejected(self):
        """UUID v3 must be rejected (version digit is not 4)."""
        uuid_v3 = "6fa459ea-ee8a-3ca4-894e-db77e160355e"
        with pytest.raises(ValidationError):
            IMFDocument(**_minimal_doc(request_id=uuid_v3))

    def test_uppercase_uuid_accepted(self):
        """Uppercase UUID is accepted — request_id validation is
        case-insensitive platform-wide (shared.imf.UUID4_RE), matching
        security_layer/intelligent_router's validators, which always
        accepted uppercase. This service's own validator used to be
        case-sensitive; that inconsistency is intentionally resolved in
        favor of the more lenient, majority behavior."""
        upper = VALID_UUID_V4.upper()
        doc = IMFDocument(**_minimal_doc(request_id=upper))
        assert doc.request_id == upper

    def test_empty_request_id_raises(self):
        with pytest.raises(ValidationError):
            IMFDocument(**_minimal_doc(request_id=""))

    def test_missing_request_id_raises(self):
        payload = {
            "user": {"user_id": "alice"},
            "request": {"messages": ONE_MESSAGE},
        }
        with pytest.raises(ValidationError):
            IMFDocument(**payload)


# ---------------------------------------------------------------------------
# IMFDocument — defaults and structure
# ---------------------------------------------------------------------------


class TestIMFDocumentDefaults:
    def test_trace_span_timestamp_default_to_empty_string(self):
        doc = IMFDocument(**_minimal_doc())
        assert doc.trace_id == ""
        assert doc.span_id == ""
        assert doc.timestamp_utc == ""

    def test_metadata_defaults_to_empty_dict(self):
        doc = IMFDocument(**_minimal_doc())
        assert doc.metadata == {}

    def test_extensions_defaults_to_empty_dict(self):
        """Req 10.1: extensions must always be present as a dict."""
        doc = IMFDocument(**_minimal_doc())
        assert doc.extensions == {}

    def test_extensions_get_agentic_returns_none_when_absent(self):
        """extensions.get('agentic') is the agentic flag gate check."""
        doc = IMFDocument(**_minimal_doc())
        assert doc.extensions.get("agentic") is None

    def test_extensions_get_agentic_returns_true_when_set(self):
        doc = IMFDocument(**_minimal_doc(extensions={"agentic": True}))
        assert doc.extensions.get("agentic") is True

    def test_extensions_agentic_false(self):
        doc = IMFDocument(**_minimal_doc(extensions={"agentic": False}))
        assert doc.extensions.get("agentic") is False

    def test_sub_models_have_defaults(self):
        doc = IMFDocument(**_minimal_doc())
        assert isinstance(doc.governance, IMFGovernance)
        assert isinstance(doc.routing, IMFRouting)
        assert isinstance(doc.cache, IMFCache)
        assert isinstance(doc.response, IMFResponse)


# ---------------------------------------------------------------------------
# IMFDocument — extra fields allowed (Req 10.3, 10.4)
# ---------------------------------------------------------------------------


class TestIMFDocumentExtraFields:
    def test_extra_top_level_field_allowed(self):
        """extra='allow' means unknown fields must not cause ValidationError."""
        payload = _minimal_doc()
        payload["some_future_field"] = "value"
        doc = IMFDocument(**payload)
        # Extra field is preserved in model_extra
        assert doc.model_extra.get("some_future_field") == "value"

    def test_model_dump_includes_known_fields(self):
        doc = IMFDocument(**_minimal_doc())
        dumped = doc.model_dump()
        assert "request_id" in dumped
        assert "extensions" in dumped
        assert "metadata" in dumped


# ---------------------------------------------------------------------------
# IMFDocument — missing required user/request blocks
# ---------------------------------------------------------------------------


class TestIMFDocumentRequiredBlocks:
    def test_missing_user_raises(self):
        payload = {
            "request_id": VALID_UUID_V4,
            "request": {"messages": ONE_MESSAGE},
        }
        with pytest.raises(ValidationError) as exc_info:
            IMFDocument(**payload)
        errors = exc_info.value.errors()
        assert any("user" in str(e["loc"]) for e in errors)

    def test_missing_request_raises(self):
        payload = {
            "request_id": VALID_UUID_V4,
            "user": {"user_id": "alice"},
        }
        with pytest.raises(ValidationError) as exc_info:
            IMFDocument(**payload)
        errors = exc_info.value.errors()
        assert any("request" in str(e["loc"]) for e in errors)

    def test_request_with_empty_messages_raises(self):
        """Req 1.3: empty messages in a nested request still raises ValidationError."""
        payload = {
            "request_id": VALID_UUID_V4,
            "user": {"user_id": "alice"},
            "request": {"messages": []},
        }
        with pytest.raises(ValidationError):
            IMFDocument(**payload)


# ---------------------------------------------------------------------------
# Round-trip: dict → model → dict
# ---------------------------------------------------------------------------


class TestIMFDocumentRoundTrip:
    def test_full_round_trip(self):
        payload = {
            "request_id": VALID_UUID_V4,
            "trace_id": "otel-trace-001",
            "span_id": "otel-span-001",
            "timestamp_utc": "2026-06-01T12:00:00Z",
            "user": {
                "user_id": "alice",
                "department": "engineering",
                "roles": ["developer"],
                "auth_method": "oidc",
            },
            "request": {
                "model": "llama3.2:3b",
                "task_type": "chat",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
                "stream": False,
                "max_tokens": 1024,
                "temperature": 0.5,
            },
            "governance": {
                "pii_masked": True,
                "content_safety_passed": True,
            },
            "routing": {
                "selected_model": "llama3.2:3b",
                "routing_mode": "pinned",
            },
            "cache": {"lookup_hit": False},
            "response": {"content": None, "finish_reason": None},
            "metadata": {"custom_key": "custom_value"},
            "extensions": {"agentic": True},
        }

        doc = IMFDocument(**payload)
        dumped = doc.model_dump()

        assert dumped["request_id"] == VALID_UUID_V4
        assert dumped["trace_id"] == "otel-trace-001"
        assert dumped["user"]["user_id"] == "alice"
        assert dumped["request"]["messages"][0]["content"] == "What is 2+2?"
        assert dumped["extensions"]["agentic"] is True
        assert dumped["metadata"]["custom_key"] == "custom_value"
        assert dumped["governance"]["pii_masked"] is True
