"""
Unit tests for security_layer/models.py

Covers (task 4.9):
1. Valid UUID-v4 strings pass IMFRequest construction
2. Non-UUID strings fail with ValidationError (ValueError about request_id)
3. request.messages absent raises ValidationError
4. request.messages as empty list raises ValidationError
5. Optional fields in UserBlock, IMFRequest.user, IMFRequest.response accept None
6. GovernanceBlock defaults are all correct
7. ResponseBlock.content = None is valid
8. Two GovernanceBlock instances have independent list instances
"""

import pytest
from pydantic import ValidationError

from security_layer.models import (
    GovernanceBlock,
    IMFRequest,
    Message,
    PostAuditEventPayload,
    PreAuditEventPayload,
    RequestBlock,
    ResponseBlock,
    UserBlock,
    UUID4_RE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"

MINIMAL_MESSAGES = [{"role": "user", "content": "hello"}]


def minimal_imf(**overrides) -> dict:
    """Return a minimal valid IMFRequest payload dict."""
    data: dict = {
        "request_id": VALID_UUID,
        "request": {"messages": MINIMAL_MESSAGES},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. Valid UUID-v4 passes construction
# ---------------------------------------------------------------------------

class TestValidUUID:
    def test_standard_uuid4_passes(self):
        imf = IMFRequest(**minimal_imf())
        assert imf.request_id == VALID_UUID

    def test_uuid4_uppercase_passes(self):
        upper = VALID_UUID.upper()
        imf = IMFRequest(**minimal_imf(request_id=upper))
        assert imf.request_id == upper

    def test_another_valid_uuid4_passes(self):
        uid = "123e4567-e89b-42d3-a456-426614174000"
        imf = IMFRequest(**minimal_imf(request_id=uid))
        assert imf.request_id == uid


# ---------------------------------------------------------------------------
# 2. Non-UUID strings fail with ValidationError / ValueError
# ---------------------------------------------------------------------------

class TestInvalidRequestId:
    def _assert_request_id_error(self, bad_value: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest(**minimal_imf(request_id=bad_value))
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("request_id",) for e in errors), (
            f"Expected error on 'request_id', got: {errors}"
        )
        # The ValueError message must mention UUID-v4
        assert any("UUID-v4" in str(e["msg"]) for e in errors), (
            f"Expected 'UUID-v4' in error message, got: {errors}"
        )

    def test_plain_string_rejected(self):
        self._assert_request_id_error("not-a-uuid")

    def test_empty_string_rejected(self):
        self._assert_request_id_error("")

    def test_uuid_version_1_rejected(self):
        # version nibble '1' instead of '4'
        self._assert_request_id_error("550e8400-e29b-11d4-a716-446655440000")

    def test_uuid_invalid_variant_rejected(self):
        # variant nibble '0' — not in [89ab]
        self._assert_request_id_error("550e8400-e29b-41d4-0716-446655440000")

    def test_uuid_missing_hyphens_rejected(self):
        self._assert_request_id_error("550e8400e29b41d4a716446655440000")

    def test_random_garbage_rejected(self):
        self._assert_request_id_error("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")


# ---------------------------------------------------------------------------
# 3. request.messages absent raises ValidationError
# ---------------------------------------------------------------------------

class TestMissingMessages:
    def test_messages_absent_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest(**minimal_imf(request={}))
        errors = exc_info.value.errors()
        # Should report that 'messages' is missing somewhere in the error chain
        locs = [str(e["loc"]) for e in errors]
        assert any("messages" in loc for loc in locs), (
            f"Expected 'messages' in error locations, got: {locs}"
        )

    def test_request_block_absent_raises(self):
        data = {"request_id": VALID_UUID}  # no 'request' key at all
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest(**data)
        errors = exc_info.value.errors()
        locs = [str(e["loc"]) for e in errors]
        assert any("request" in loc for loc in locs)


# ---------------------------------------------------------------------------
# 4. request.messages as empty list raises ValidationError
# ---------------------------------------------------------------------------

class TestEmptyMessages:
    def test_empty_messages_list_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            IMFRequest(**minimal_imf(request={"messages": []}))
        errors = exc_info.value.errors()
        locs = [str(e["loc"]) for e in errors]
        assert any("messages" in loc for loc in locs), (
            f"Expected 'messages' in error locations, got: {locs}"
        )

    def test_request_block_empty_messages_raises(self):
        with pytest.raises(ValidationError):
            RequestBlock(messages=[])


# ---------------------------------------------------------------------------
# 5. Optional fields accept None
# ---------------------------------------------------------------------------

class TestOptionalFields:
    def test_user_block_all_none(self):
        ub = UserBlock()
        assert ub.user_id is None
        assert ub.department is None
        assert ub.roles is None
        assert ub.auth_method is None

    def test_imf_user_none(self):
        imf = IMFRequest(**minimal_imf(user=None))
        assert imf.user is None

    def test_imf_response_none(self):
        imf = IMFRequest(**minimal_imf(response=None))
        assert imf.response is None

    def test_imf_user_omitted(self):
        imf = IMFRequest(**minimal_imf())
        assert imf.user is None

    def test_imf_response_omitted(self):
        imf = IMFRequest(**minimal_imf())
        assert imf.response is None

    def test_response_block_content_none(self):
        rb = ResponseBlock(content=None)
        assert rb.content is None

    def test_response_block_finish_reason_none(self):
        rb = ResponseBlock(finish_reason=None)
        assert rb.finish_reason is None


# ---------------------------------------------------------------------------
# 6. GovernanceBlock defaults are all correct
# ---------------------------------------------------------------------------

class TestGovernanceBlockDefaults:
    def setup_method(self):
        self.gb = GovernanceBlock()

    def test_pii_masked_default_false(self):
        assert self.gb.pii_masked is False

    def test_pii_fields_detected_default_empty_list(self):
        assert self.gb.pii_fields_detected == []

    def test_injection_score_default_zero(self):
        assert self.gb.injection_score == 0.0

    def test_jailbreak_score_default_zero(self):
        assert self.gb.jailbreak_score == 0.0

    def test_content_safety_passed_default_true(self):
        assert self.gb.content_safety_passed is True

    def test_human_approval_required_default_false(self):
        assert self.gb.human_approval_required is False

    def test_human_approval_status_default_not_required(self):
        assert self.gb.human_approval_status == "not_required"

    def test_policy_decisions_default_empty_list(self):
        assert self.gb.policy_decisions == []


# ---------------------------------------------------------------------------
# 7. ResponseBlock.content = None is valid
# ---------------------------------------------------------------------------

class TestResponseBlockNullContent:
    def test_content_none_explicit(self):
        rb = ResponseBlock(content=None)
        assert rb.content is None

    def test_content_none_default(self):
        rb = ResponseBlock()
        assert rb.content is None

    def test_content_string_is_valid(self):
        rb = ResponseBlock(content="some response text")
        assert rb.content == "some response text"


# ---------------------------------------------------------------------------
# 8. Two GovernanceBlock instances have independent list instances
# ---------------------------------------------------------------------------

class TestGovernanceBlockMutableDefaults:
    def test_pii_fields_detected_independent(self):
        gb1 = GovernanceBlock()
        gb2 = GovernanceBlock()
        gb1.pii_fields_detected.append("EMAIL_ADDRESS")
        assert gb2.pii_fields_detected == [], (
            "Mutating gb1.pii_fields_detected must not affect gb2"
        )

    def test_policy_decisions_independent(self):
        gb1 = GovernanceBlock()
        gb2 = GovernanceBlock()
        gb1.policy_decisions.append("role_check_pass")
        assert gb2.policy_decisions == [], (
            "Mutating gb1.policy_decisions must not affect gb2"
        )

    def test_lists_are_not_the_same_object(self):
        gb1 = GovernanceBlock()
        gb2 = GovernanceBlock()
        assert gb1.pii_fields_detected is not gb2.pii_fields_detected
        assert gb1.policy_decisions is not gb2.policy_decisions


# ---------------------------------------------------------------------------
# Bonus: UUID4_RE regex sanity checks (mirrors audit_store test pattern)
# ---------------------------------------------------------------------------

class TestUUID4RE:
    def test_valid_uuid4_matches(self):
        assert UUID4_RE.match(VALID_UUID)

    def test_version_1_does_not_match(self):
        assert not UUID4_RE.match("550e8400-e29b-11d4-a716-446655440000")

    def test_random_string_does_not_match(self):
        assert not UUID4_RE.match("not-a-uuid")

    def test_empty_string_does_not_match(self):
        assert not UUID4_RE.match("")


# ---------------------------------------------------------------------------
# Audit payload smoke tests
# ---------------------------------------------------------------------------

class TestAuditPayloads:
    def test_pre_audit_event_construction(self):
        payload = PreAuditEventPayload(
            request_id=VALID_UUID,
            event_type="request_received",
            outcome="pass",
            timestamp_utc="2024-01-01T00:00:00Z",
            latency_ms=42,
        )
        assert payload.layer == "security"
        assert payload.pii_actions == []
        assert payload.policy_decisions == []

    def test_post_audit_event_construction(self):
        payload = PostAuditEventPayload(
            request_id=VALID_UUID,
            event_type="response_sent",
            outcome="pass",
            timestamp_utc="2024-01-01T00:00:00Z",
            latency_ms=10,
            pii_actions=["EMAIL_ADDRESS"],
        )
        assert payload.layer == "security"
        assert payload.pii_actions == ["EMAIL_ADDRESS"]

    def test_pre_audit_pii_actions_independent(self):
        p1 = PreAuditEventPayload(
            request_id=VALID_UUID,
            event_type="request_received",
            outcome="pass",
            timestamp_utc="2024-01-01T00:00:00Z",
            latency_ms=0,
        )
        p2 = PreAuditEventPayload(
            request_id=VALID_UUID,
            event_type="request_received",
            outcome="pass",
            timestamp_utc="2024-01-01T00:00:00Z",
            latency_ms=0,
        )
        p1.pii_actions.append("PERSON")
        assert p2.pii_actions == []
