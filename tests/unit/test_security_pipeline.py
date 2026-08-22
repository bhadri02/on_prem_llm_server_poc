"""
Unit tests for security_layer.pipeline.

Covers subtask 11.4:
- Injection block short-circuits before content safety
- Content safety block short-circuits before PII and policy
- PII always runs before policy on passing requests
- Policy deny returns correct block_status=403
- All governance fields present and correctly typed on a passing pipeline
- run_post_pipeline with null response.content returns IMF unchanged with empty entity list
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure required env vars are present before importing the security_layer package.
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

from security_layer.pipeline import (  # noqa: E402
    PipelineResult,
    run_pre_pipeline,
    run_post_pipeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_imf(roles=None, messages=None):
    """Return a minimal valid IMF dict suitable for pipeline testing."""
    return {
        "request_id": "11111111-1111-4111-8111-111111111111",
        "user": {"user_id": "u1", "roles": roles or ["developer"]},
        "request": {
            "messages": messages or [{"role": "user", "content": "Hello"}],
        },
        "governance": {
            "injection_score": 0.0,
            "content_safety_passed": True,
            "pii_masked": False,
            "pii_fields_detected": [],
            "policy_decisions": [],
            "human_approval_required": False,
            "human_approval_status": "not_required",
        },
        "response": {"content": None},
        "metadata": {},
        "extensions": {},
    }


def _make_state(
    *,
    injection_score: float = 0.0,
    content_safe: bool = True,
    pii_entities: list[str] | None = None,
    policy_permitted: bool = True,
    pii_enabled: bool = True,
):
    """
    Build a SimpleNamespace mock for app.state that patches the four pipeline
    functions with controllable return values.

    The ``patterns`` and ``blocklist`` attributes are real lists so that the
    actual ``scan_for_injection`` / ``check_content_safety`` functions behave
    naturally.  We still inject via mock patches on the module to control
    outcomes precisely.
    """
    settings = SimpleNamespace(pii_enabled=pii_enabled, pii_entities_list=["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"])
    state = SimpleNamespace(
        patterns=[],
        blocklist=[],
        analyzer=MagicMock(),
        anonymizer=MagicMock(),
        settings=settings,
    )
    # Configure mock return values on the analyzer/anonymizer for mask_messages
    # and mask_text calls so that pii_entities can be controlled.
    if pii_entities is None:
        pii_entities = []
    # We'll patch the module-level functions in pipeline.py to control outcomes.
    return state, {
        "injection_score": injection_score,
        "content_safe": content_safe,
        "pii_entities": pii_entities,
        "policy_permitted": policy_permitted,
    }


def _patch_pipeline_deps(imf_messages, injection_score, content_safe, pii_entities, policy_permitted):
    """
    Return a context-manager stack (via a helper dict) of patches for the four
    pipeline dependency functions.  This lets each test control exactly what
    each stage returns without real Presidio or pattern files.
    """
    masked_messages = imf_messages  # identity — no actual masking needed for unit tests

    patch_injection = patch(
        "security_layer.pipeline.scan_for_injection",
        return_value=injection_score,
    )
    patch_content = patch(
        "security_layer.pipeline.check_content_safety",
        return_value=content_safe,
    )
    patch_pii = patch(
        "security_layer.pipeline.mask_messages",
        return_value=(masked_messages, pii_entities),
    )
    policy_decision = "role_check_pass" if policy_permitted else "role_check_deny"
    patch_policy = patch(
        "security_layer.pipeline.check_policy",
        return_value=(policy_permitted, policy_decision),
    )
    return patch_injection, patch_content, patch_pii, patch_policy


# ---------------------------------------------------------------------------
# PipelineResult dataclass
# ---------------------------------------------------------------------------

class TestPipelineResultDataclass:
    """Verify the dataclass fields exist and accept the correct types."""

    def test_blocked_true_result(self):
        imf = _base_imf()
        result = PipelineResult(
            blocked=True,
            block_reason="injection_detected",
            block_status=400,
            imf=imf,
            latency_ms=5,
        )
        assert result.blocked is True
        assert result.block_reason == "injection_detected"
        assert result.block_status == 400
        assert result.imf is imf
        assert isinstance(result.latency_ms, int)

    def test_non_blocked_result(self):
        imf = _base_imf()
        result = PipelineResult(
            blocked=False,
            block_reason=None,
            block_status=None,
            imf=imf,
            latency_ms=10,
        )
        assert result.blocked is False
        assert result.block_reason is None
        assert result.block_status is None

    def test_block_reasons_are_valid_strings(self):
        valid_reasons = {"injection_detected", "content_safety_violation", "policy_denied"}
        for reason in valid_reasons:
            result = PipelineResult(
                blocked=True, block_reason=reason, block_status=400,
                imf={}, latency_ms=1,
            )
            assert result.block_reason in valid_reasons


# ---------------------------------------------------------------------------
# run_pre_pipeline — injection block short-circuits before content safety
# ---------------------------------------------------------------------------

class TestInjectionBlockShortCircuit:
    """
    When injection_score == 1.0, the pipeline MUST block immediately.
    Content safety, PII masking, and policy check MUST NOT run.
    """

    @pytest.mark.asyncio
    async def test_injection_block_returns_blocked_result(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0,
            content_safe=True,
            pii_entities=[],
            policy_permitted=True,
        )
        with patches[0] as mock_inj, patches[1] as mock_cs, patches[2] as mock_pii, patches[3] as mock_pol:
            result = await run_pre_pipeline(imf, state)

        assert result.blocked is True
        assert result.block_reason == "injection_detected"
        assert result.block_status == 400

    @pytest.mark.asyncio
    async def test_injection_block_sets_injection_score_in_imf(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        assert imf["governance"]["injection_score"] == 1.0

    @pytest.mark.asyncio
    async def test_injection_block_does_not_call_content_safety(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1] as mock_cs, patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        mock_cs.assert_not_called()

    @pytest.mark.asyncio
    async def test_injection_block_does_not_call_pii(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2] as mock_pii, patches[3]:
            await run_pre_pipeline(imf, state)

        mock_pii.assert_not_called()

    @pytest.mark.asyncio
    async def test_injection_block_does_not_call_policy(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3] as mock_pol:
            await run_pre_pipeline(imf, state)

        mock_pol.assert_not_called()

    @pytest.mark.asyncio
    async def test_latency_ms_is_non_negative_integer(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# run_pre_pipeline — content safety block short-circuits before PII and policy
# ---------------------------------------------------------------------------

class TestContentSafetyBlockShortCircuit:
    """
    When content safety returns False, the pipeline MUST block immediately.
    PII masking and policy check MUST NOT run.
    """

    @pytest.mark.asyncio
    async def test_content_safety_block_returns_blocked_result(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=False, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.blocked is True
        assert result.block_reason == "content_safety_violation"
        assert result.block_status == 400

    @pytest.mark.asyncio
    async def test_content_safety_block_sets_flag_in_imf(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=False, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        assert imf["governance"]["content_safety_passed"] is False

    @pytest.mark.asyncio
    async def test_content_safety_block_does_not_call_pii(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=False, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2] as mock_pii, patches[3]:
            await run_pre_pipeline(imf, state)

        mock_pii.assert_not_called()

    @pytest.mark.asyncio
    async def test_content_safety_block_does_not_call_policy(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=False, pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3] as mock_pol:
            await run_pre_pipeline(imf, state)

        mock_pol.assert_not_called()


# ---------------------------------------------------------------------------
# run_pre_pipeline — PII runs before policy on passing requests
# ---------------------------------------------------------------------------

class TestPiiRunsBeforePolicy:
    """
    When injection and content safety pass, PII MUST run before policy check.
    """

    @pytest.mark.asyncio
    async def test_pii_is_called_before_policy_on_passing_request(self):
        """Use call order tracking to confirm PII precedes policy."""
        imf = _base_imf()
        state, _ = _make_state()
        call_order: list[str] = []

        def pii_side_effect(*args, **kwargs):
            call_order.append("pii")
            return (imf["request"]["messages"], [])

        def policy_side_effect(*args, **kwargs):
            call_order.append("policy")
            return (True, "role_check_pass")

        with (
            patch("security_layer.pipeline.scan_for_injection", return_value=0.0),
            patch("security_layer.pipeline.check_content_safety", return_value=True),
            patch("security_layer.pipeline.mask_messages", side_effect=pii_side_effect),
            patch("security_layer.pipeline.check_policy", side_effect=policy_side_effect),
        ):
            await run_pre_pipeline(imf, state)

        assert call_order == ["pii", "policy"], (
            f"Expected PII before policy, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_pii_fields_set_in_imf_on_passing_request(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=["EMAIL_ADDRESS"], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.blocked is False
        assert imf["governance"]["pii_masked"] is True
        assert "EMAIL_ADDRESS" in imf["governance"]["pii_fields_detected"]

    @pytest.mark.asyncio
    async def test_pii_called_even_when_no_entities_detected(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2] as mock_pii, patches[3]:
            await run_pre_pipeline(imf, state)

        mock_pii.assert_called_once()

    @pytest.mark.asyncio
    async def test_pii_masked_false_when_no_entities(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        assert imf["governance"]["pii_masked"] is False
        assert imf["governance"]["pii_fields_detected"] == []


# ---------------------------------------------------------------------------
# run_pre_pipeline — policy deny returns block_status=403
# ---------------------------------------------------------------------------

class TestPolicyDenyBlock:
    """Policy denial MUST return block_status=403 and block_reason='policy_denied'."""

    @pytest.mark.asyncio
    async def test_policy_deny_returns_403(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=False,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.blocked is True
        assert result.block_status == 403
        assert result.block_reason == "policy_denied"

    @pytest.mark.asyncio
    async def test_policy_deny_appends_decision_to_imf(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=False,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        assert "role_check_deny" in imf["governance"]["policy_decisions"]

    @pytest.mark.asyncio
    async def test_policy_deny_pii_already_ran(self):
        """PII masking must have run before the policy denial block is triggered."""
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=["PHONE_NUMBER"], policy_permitted=False,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        # Policy denied, but PII fields must already be set
        assert result.blocked is True
        assert imf["governance"]["pii_fields_detected"] == ["PHONE_NUMBER"]

    @pytest.mark.asyncio
    async def test_injection_block_returns_400_not_403(self):
        """Injection block must return 400, not 403."""
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=1.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.block_status == 400

    @pytest.mark.asyncio
    async def test_content_safety_block_returns_400_not_403(self):
        """Content safety block must return 400, not 403."""
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=False,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.block_status == 400


# ---------------------------------------------------------------------------
# run_pre_pipeline — all governance fields on a passing pipeline
# ---------------------------------------------------------------------------

class TestPassingPipelineGovernanceFields:
    """
    On a fully passing pipeline, every expected governance field must be
    present in the IMF with the correct type.
    """

    @pytest.mark.asyncio
    async def test_all_governance_fields_present_and_typed(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=["EMAIL_ADDRESS"], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        gov = imf["governance"]

        assert result.blocked is False
        assert result.block_reason is None
        assert result.block_status is None

        # injection_score: float
        assert isinstance(gov["injection_score"], float)
        assert gov["injection_score"] == 0.0

        # content_safety_passed: bool
        assert isinstance(gov["content_safety_passed"], bool)
        assert gov["content_safety_passed"] is True

        # pii_masked: bool
        assert isinstance(gov["pii_masked"], bool)
        assert gov["pii_masked"] is True  # because entities = ["EMAIL_ADDRESS"]

        # pii_fields_detected: list
        assert isinstance(gov["pii_fields_detected"], list)
        assert "EMAIL_ADDRESS" in gov["pii_fields_detected"]

        # policy_decisions: list
        assert isinstance(gov["policy_decisions"], list)
        assert len(gov["policy_decisions"]) >= 1

        # human_approval_required: bool, False for POC
        assert isinstance(gov["human_approval_required"], bool)
        assert gov["human_approval_required"] is False

        # human_approval_status: str, "not_required" for POC
        assert isinstance(gov["human_approval_status"], str)
        assert gov["human_approval_status"] == "not_required"

    @pytest.mark.asyncio
    async def test_passing_result_has_non_negative_latency(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_passing_result_imf_is_same_object(self):
        """run_pre_pipeline mutates the IMF in-place; result.imf must be the same dict."""
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = await run_pre_pipeline(imf, state)

        assert result.imf is imf

    @pytest.mark.asyncio
    async def test_policy_pass_decision_appended(self):
        imf = _base_imf()
        state, _ = _make_state()
        patches = _patch_pipeline_deps(
            imf["request"]["messages"],
            injection_score=0.0, content_safe=True,
            pii_entities=[], policy_permitted=True,
        )
        with patches[0], patches[1], patches[2], patches[3]:
            await run_pre_pipeline(imf, state)

        assert "role_check_pass" in imf["governance"]["policy_decisions"]


# ---------------------------------------------------------------------------
# run_post_pipeline — null / missing response.content
# ---------------------------------------------------------------------------

class TestRunPostPipelineNullContent:
    """
    When response.content is null, missing, or empty the IMF MUST be returned
    unchanged and the entity list MUST be empty.
    """

    @pytest.mark.asyncio
    async def test_null_content_returns_imf_unchanged(self):
        imf = _base_imf()
        imf["response"] = {"content": None}
        state, _ = _make_state()

        result_imf, entities = await run_post_pipeline(imf, state)

        assert result_imf is imf
        assert entities == []

    @pytest.mark.asyncio
    async def test_absent_response_key_returns_imf_unchanged(self):
        imf = _base_imf()
        del imf["response"]
        state, _ = _make_state()

        result_imf, entities = await run_post_pipeline(imf, state)

        assert result_imf is imf
        assert entities == []

    @pytest.mark.asyncio
    async def test_empty_string_content_returns_imf_unchanged(self):
        imf = _base_imf()
        imf["response"] = {"content": ""}
        state, _ = _make_state()

        result_imf, entities = await run_post_pipeline(imf, state)

        assert entities == []

    @pytest.mark.asyncio
    async def test_null_response_block_returns_imf_unchanged(self):
        imf = _base_imf()
        imf["response"] = None
        state, _ = _make_state()

        result_imf, entities = await run_post_pipeline(imf, state)

        assert result_imf is imf
        assert entities == []

    @pytest.mark.asyncio
    async def test_mask_text_not_called_when_content_null(self):
        imf = _base_imf()
        imf["response"] = {"content": None}
        state, _ = _make_state()

        with patch("security_layer.pipeline.mask_text") as mock_mask:
            await run_post_pipeline(imf, state)

        mock_mask.assert_not_called()


# ---------------------------------------------------------------------------
# run_post_pipeline — content present, PII found
# ---------------------------------------------------------------------------

class TestRunPostPipelineWithContent:
    """
    When response.content is non-null and PII is found, the IMF content MUST
    be replaced and governance fields updated.
    """

    @pytest.mark.asyncio
    async def test_content_masked_and_entities_returned(self):
        imf = _base_imf()
        imf["response"] = {"content": "Call me at 555-867-5309"}
        state, _ = _make_state()

        with patch(
            "security_layer.pipeline.mask_text",
            return_value=("Call me at [REDACTED_PHONE_NUMBER]", ["PHONE_NUMBER"]),
        ):
            result_imf, entities = await run_post_pipeline(imf, state)

        assert result_imf["response"]["content"] == "Call me at [REDACTED_PHONE_NUMBER]"
        assert entities == ["PHONE_NUMBER"]

    @pytest.mark.asyncio
    async def test_pii_masked_set_true_in_governance(self):
        imf = _base_imf()
        imf["response"] = {"content": "Email: user@example.com"}
        state, _ = _make_state()

        with patch(
            "security_layer.pipeline.mask_text",
            return_value=("[REDACTED_EMAIL_ADDRESS]", ["EMAIL_ADDRESS"]),
        ):
            await run_post_pipeline(imf, state)

        assert imf["governance"]["pii_masked"] is True

    @pytest.mark.asyncio
    async def test_pii_fields_detected_merged_with_existing(self):
        """New entities are merged (union, deduplicated) with pre-existing ones."""
        imf = _base_imf()
        imf["governance"]["pii_fields_detected"] = ["EMAIL_ADDRESS"]
        imf["response"] = {"content": "Call 555-867-5309"}
        state, _ = _make_state()

        with patch(
            "security_layer.pipeline.mask_text",
            return_value=("Call [REDACTED_PHONE_NUMBER]", ["PHONE_NUMBER"]),
        ):
            await run_post_pipeline(imf, state)

        detected = imf["governance"]["pii_fields_detected"]
        assert "EMAIL_ADDRESS" in detected
        assert "PHONE_NUMBER" in detected

    @pytest.mark.asyncio
    async def test_pii_fields_not_duplicated_on_merge(self):
        """Merging the same entity type twice must not produce duplicates."""
        imf = _base_imf()
        imf["governance"]["pii_fields_detected"] = ["EMAIL_ADDRESS"]
        imf["response"] = {"content": "Email: user@example.com"}
        state, _ = _make_state()

        with patch(
            "security_layer.pipeline.mask_text",
            return_value=("[REDACTED_EMAIL_ADDRESS]", ["EMAIL_ADDRESS"]),
        ):
            await run_post_pipeline(imf, state)

        detected = imf["governance"]["pii_fields_detected"]
        assert detected.count("EMAIL_ADDRESS") == 1

    @pytest.mark.asyncio
    async def test_no_entities_found_does_not_mutate_governance(self):
        """When mask_text finds no entities, governance fields must not change."""
        imf = _base_imf()
        original_pii_masked = imf["governance"]["pii_masked"]
        imf["response"] = {"content": "Hello there"}
        state, _ = _make_state()

        with patch(
            "security_layer.pipeline.mask_text",
            return_value=("Hello there", []),
        ):
            result_imf, entities = await run_post_pipeline(imf, state)

        assert entities == []
        assert result_imf["governance"]["pii_masked"] == original_pii_masked
