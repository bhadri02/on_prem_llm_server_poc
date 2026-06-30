"""
Unit tests for audit_store/models.py

Covers:
- Valid UUID-v4 strings pass request_id validation
- Non-UUID strings raise ValueError / ValidationError
- Invalid enum values (layer, event_type, outcome) are rejected
- Missing required fields are rejected
- Optional fields accept None
- pii_actions and policy_decisions default to []
- BatchWriteRequest rejects empty list and list > 500
"""

import pytest
from pydantic import ValidationError

from audit_store.models import (
    AuditEventCreate,
    AuditEventResponse,
    BatchWriteRequest,
    BatchWriteResponse,
    LayerEnum,
    EventTypeEnum,
    OutcomeEnum,
    SummaryResponse,
    UUID4_RE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"

MINIMAL_VALID = {
    "request_id": VALID_UUID,
    "layer": "api_gateway",
    "event_type": "request_received",
    "outcome": "pass",
}


def make_event(**overrides) -> dict:
    data = dict(MINIMAL_VALID)
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 5.4  UUID4_RE regex
# ---------------------------------------------------------------------------

class TestUUID4Regex:
    def test_valid_uuid4_matches(self):
        assert UUID4_RE.match("550e8400-e29b-41d4-a716-446655440000")

    def test_valid_uuid4_uppercase_matches(self):
        assert UUID4_RE.match("550E8400-E29B-41D4-A716-446655440000")

    def test_version_1_uuid_does_not_match(self):
        # version nibble is '1', not '4'
        assert not UUID4_RE.match("550e8400-e29b-11d4-a716-446655440000")

    def test_random_string_does_not_match(self):
        assert not UUID4_RE.match("not-a-uuid")

    def test_empty_string_does_not_match(self):
        assert not UUID4_RE.match("")

    def test_missing_hyphens_does_not_match(self):
        assert not UUID4_RE.match("550e8400e29b41d4a716446655440000")


# ---------------------------------------------------------------------------
# 5.6  request_id validator
# ---------------------------------------------------------------------------

class TestRequestIdValidator:
    def test_valid_uuid_passes(self):
        event = AuditEventCreate(**make_event())
        assert event.request_id == VALID_UUID

    def test_valid_uuid_lowercase_passes(self):
        uid = "123e4567-e89b-42d3-a456-426614174000"
        event = AuditEventCreate(**make_event(request_id=uid))
        assert event.request_id == uid

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(request_id=""))
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("request_id",) for e in errors)
        assert any("UUID-v4" in str(e["msg"]) for e in errors)

    def test_plain_string_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(request_id="not-a-uuid"))
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("request_id",) for e in errors)

    def test_uuid_wrong_version_raises(self):
        # version nibble '1' instead of '4'
        bad_uuid = "550e8400-e29b-11d4-a716-446655440000"
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(request_id=bad_uuid))
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("request_id",) for e in errors)

    def test_uuid_with_invalid_variant_raises(self):
        # variant nibble '0' — not in [89ab]
        bad_uuid = "550e8400-e29b-41d4-0716-446655440000"
        with pytest.raises(ValidationError):
            AuditEventCreate(**make_event(request_id=bad_uuid))


# ---------------------------------------------------------------------------
# 5.1 – 5.3  Enum validation
# ---------------------------------------------------------------------------

class TestLayerEnum:
    @pytest.mark.parametrize("value", [v.value for v in LayerEnum])
    def test_all_valid_layer_values_accepted(self, value):
        event = AuditEventCreate(**make_event(layer=value))
        assert event.layer.value == value

    def test_invalid_layer_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(layer="unknown_layer"))
        errors = exc_info.value.errors()
        assert any("layer" in str(e["loc"]) for e in errors)

    def test_empty_layer_rejected(self):
        with pytest.raises(ValidationError):
            AuditEventCreate(**make_event(layer=""))


class TestEventTypeEnum:
    @pytest.mark.parametrize("value", [v.value for v in EventTypeEnum])
    def test_all_valid_event_type_values_accepted(self, value):
        event = AuditEventCreate(**make_event(event_type=value))
        assert event.event_type.value == value

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(event_type="bad_event"))
        errors = exc_info.value.errors()
        assert any("event_type" in str(e["loc"]) for e in errors)


class TestOutcomeEnum:
    def test_pass_value_accepted(self):
        event = AuditEventCreate(**make_event(outcome="pass"))
        assert event.outcome == OutcomeEnum.pass_
        assert event.outcome.value == "pass"

    def test_block_value_accepted(self):
        event = AuditEventCreate(**make_event(outcome="block"))
        assert event.outcome == OutcomeEnum.block

    def test_flag_value_accepted(self):
        event = AuditEventCreate(**make_event(outcome="flag"))
        assert event.outcome == OutcomeEnum.flag

    def test_invalid_outcome_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**make_event(outcome="unknown"))
        errors = exc_info.value.errors()
        assert any("outcome" in str(e["loc"]) for e in errors)

    def test_outcome_pass_serialised_as_pass_string(self):
        event = AuditEventCreate(**make_event(outcome="pass"))
        dumped = event.model_dump()
        assert dumped["outcome"] == OutcomeEnum.pass_  # enum member


# ---------------------------------------------------------------------------
# 5.5  Required fields
# ---------------------------------------------------------------------------

class TestRequiredFields:
    @pytest.mark.parametrize("missing_field", ["request_id", "layer", "event_type", "outcome"])
    def test_missing_required_field_rejected(self, missing_field):
        data = dict(MINIMAL_VALID)
        del data[missing_field]
        with pytest.raises(ValidationError) as exc_info:
            AuditEventCreate(**data)
        errors = exc_info.value.errors()
        assert any(missing_field in str(e["loc"]) for e in errors)


# ---------------------------------------------------------------------------
# 5.5  Optional fields accept None
# ---------------------------------------------------------------------------

class TestOptionalFields:
    @pytest.mark.parametrize("field", [
        "audit_id", "timestamp_utc", "user_id", "department", "model_used", "error_code"
    ])
    def test_optional_field_accepts_none(self, field):
        event = AuditEventCreate(**make_event(**{field: None}))
        assert getattr(event, field) is None

    def test_all_optional_fields_omitted_uses_defaults(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.audit_id is None
        assert event.timestamp_utc is None
        assert event.user_id is None
        assert event.department is None
        assert event.model_used is None
        assert event.error_code is None


# ---------------------------------------------------------------------------
# 5.5  Integer fields default to 0
# ---------------------------------------------------------------------------

class TestIntegerDefaults:
    def test_prompt_tokens_defaults_to_zero(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.prompt_tokens == 0

    def test_completion_tokens_defaults_to_zero(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.completion_tokens == 0

    def test_latency_ms_defaults_to_zero(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.latency_ms == 0


# ---------------------------------------------------------------------------
# 5.5  List fields default to []
# ---------------------------------------------------------------------------

class TestListFieldDefaults:
    def test_pii_actions_defaults_to_empty_list(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.pii_actions == []

    def test_policy_decisions_defaults_to_empty_list(self):
        event = AuditEventCreate(**MINIMAL_VALID)
        assert event.policy_decisions == []

    def test_pii_actions_instances_are_independent(self):
        e1 = AuditEventCreate(**MINIMAL_VALID)
        e2 = AuditEventCreate(**MINIMAL_VALID)
        e1.pii_actions.append("something")
        assert e2.pii_actions == []

    def test_list_fields_accept_values(self):
        event = AuditEventCreate(**make_event(
            pii_actions=["mask_email"],
            policy_decisions=[{"policy": "deny"}],
        ))
        assert event.pii_actions == ["mask_email"]
        assert event.policy_decisions == [{"policy": "deny"}]


# ---------------------------------------------------------------------------
# 5.7  AuditEventResponse
# ---------------------------------------------------------------------------

class TestAuditEventResponse:
    def test_response_requires_audit_id(self):
        # audit_id must be a non-None str in AuditEventResponse
        with pytest.raises(ValidationError):
            AuditEventResponse(**make_event(audit_id=None, timestamp_utc=None))

    def test_response_requires_timestamp_utc(self):
        with pytest.raises(ValidationError):
            AuditEventResponse(**make_event(
                audit_id="550e8400-e29b-41d4-a716-446655440001",
                timestamp_utc=None,
            ))

    def test_valid_response_model(self):
        resp = AuditEventResponse(**make_event(
            audit_id="550e8400-e29b-41d4-a716-446655440001",
            timestamp_utc="2024-01-01T00:00:00Z",
        ))
        assert resp.audit_id == "550e8400-e29b-41d4-a716-446655440001"
        assert resp.timestamp_utc == "2024-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 5.8  BatchWriteRequest
# ---------------------------------------------------------------------------

class TestBatchWriteRequest:
    def test_single_event_accepted(self):
        req = BatchWriteRequest(events=[AuditEventCreate(**MINIMAL_VALID)])
        assert len(req.events) == 1

    def test_500_events_accepted(self):
        events = [AuditEventCreate(**MINIMAL_VALID) for _ in range(500)]
        req = BatchWriteRequest(events=events)
        assert len(req.events) == 500

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            BatchWriteRequest(events=[])
        errors = exc_info.value.errors()
        assert any("events" in str(e["loc"]) for e in errors)

    def test_501_events_rejected(self):
        events = [AuditEventCreate(**MINIMAL_VALID) for _ in range(501)]
        with pytest.raises(ValidationError) as exc_info:
            BatchWriteRequest(events=events)
        errors = exc_info.value.errors()
        assert any("events" in str(e["loc"]) for e in errors)

    def test_600_events_rejected(self):
        events = [AuditEventCreate(**MINIMAL_VALID) for _ in range(600)]
        with pytest.raises(ValidationError):
            BatchWriteRequest(events=events)


# ---------------------------------------------------------------------------
# 5.9  BatchWriteResponse
# ---------------------------------------------------------------------------

class TestBatchWriteResponse:
    def test_valid_batch_write_response(self):
        resp = BatchWriteResponse(
            inserted=2,
            audit_ids=["id-1", "id-2"],
        )
        assert resp.inserted == 2
        assert resp.audit_ids == ["id-1", "id-2"]

    def test_missing_inserted_rejected(self):
        with pytest.raises(ValidationError):
            BatchWriteResponse(audit_ids=["id-1"])

    def test_missing_audit_ids_rejected(self):
        with pytest.raises(ValidationError):
            BatchWriteResponse(inserted=1)


# ---------------------------------------------------------------------------
# 5.10  SummaryResponse
# ---------------------------------------------------------------------------

class TestSummaryResponse:
    def test_valid_summary_response(self):
        resp = SummaryResponse(
            total_events=10,
            by_outcome={"pass": 8, "block": 2},
            by_layer={"api_gateway": 5, "inference": 5},
        )
        assert resp.total_events == 10
        assert resp.by_outcome["pass"] == 8
        assert resp.by_layer["api_gateway"] == 5

    def test_empty_dicts_accepted(self):
        resp = SummaryResponse(total_events=0, by_outcome={}, by_layer={})
        assert resp.total_events == 0

    def test_missing_total_events_rejected(self):
        with pytest.raises(ValidationError):
            SummaryResponse(by_outcome={}, by_layer={})
