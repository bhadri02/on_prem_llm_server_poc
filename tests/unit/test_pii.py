"""
Unit tests for security_layer.pii.

Covers:
- pii_enabled=False returns original text unchanged with empty entity list
- Empty text returns unchanged with empty entity list
- Text with email is masked to [REDACTED_EMAIL_ADDRESS]
- Entity types are deduplicated
- mask_messages processes all messages and aggregates entity types
"""

import os
from unittest.mock import MagicMock

import pytest

# Set required env vars before any security_layer import so that
# security_layer.config.Settings() can instantiate without raising.
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

from security_layer.pii import (  # noqa: E402
    POC_ENTITIES,
    MIN_CONFIDENCE,
    mask_text,
    mask_messages,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_analyzer_result(entity_type: str, start: int, end: int, score: float = 0.85):
    """Build a minimal RecognizerResult for use in mock return values."""
    result = MagicMock()
    result.entity_type = entity_type
    result.start = start
    result.end = end
    result.score = score
    return result


def _make_anonymized(text: str):
    """Build a minimal anonymized result mock."""
    anon = MagicMock()
    anon.text = text
    return anon


def _make_engines(analyze_return=None, anonymize_text=""):
    """Return (analyzer_mock, anonymizer_mock) pre-configured."""
    analyzer = MagicMock()
    anonymizer = MagicMock()
    analyzer.analyze.return_value = analyze_return if analyze_return is not None else []
    anonymizer.anonymize.return_value = _make_anonymized(anonymize_text)
    return analyzer, anonymizer


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_poc_entities_contains_required_types(self):
        assert "EMAIL_ADDRESS" in POC_ENTITIES
        assert "PHONE_NUMBER" in POC_ENTITIES
        assert "PERSON" in POC_ENTITIES

    def test_poc_entities_length(self):
        assert len(POC_ENTITIES) == 3

    def test_min_confidence_is_0_7(self):
        assert MIN_CONFIDENCE == 0.7


# ---------------------------------------------------------------------------
# mask_text — pii_enabled=False
# ---------------------------------------------------------------------------

class TestMaskTextPiiDisabled:
    """When pii_enabled=False, mask_text must not call Presidio at all."""

    def test_returns_original_text_unchanged(self):
        analyzer, anonymizer = _make_engines()
        text = "My email is user@example.com"
        result_text, entities = mask_text(text, analyzer, anonymizer, pii_enabled=False)

        assert result_text == text

    def test_returns_empty_entity_list(self):
        analyzer, anonymizer = _make_engines()
        _, entities = mask_text("some text", analyzer, anonymizer, pii_enabled=False)

        assert entities == []

    def test_analyzer_not_called(self):
        analyzer, anonymizer = _make_engines()
        mask_text("user@example.com", analyzer, anonymizer, pii_enabled=False)

        analyzer.analyze.assert_not_called()

    def test_anonymizer_not_called(self):
        analyzer, anonymizer = _make_engines()
        mask_text("user@example.com", analyzer, anonymizer, pii_enabled=False)

        anonymizer.anonymize.assert_not_called()


# ---------------------------------------------------------------------------
# mask_text — empty text
# ---------------------------------------------------------------------------

class TestMaskTextEmptyInput:
    """Empty text must be returned unchanged without calling Presidio."""

    def test_empty_string_returns_empty_string(self):
        analyzer, anonymizer = _make_engines()
        result_text, entities = mask_text("", analyzer, anonymizer, pii_enabled=True)

        assert result_text == ""

    def test_empty_string_returns_empty_entity_list(self):
        analyzer, anonymizer = _make_engines()
        _, entities = mask_text("", analyzer, anonymizer, pii_enabled=True)

        assert entities == []

    def test_analyzer_not_called_on_empty_text(self):
        analyzer, anonymizer = _make_engines()
        mask_text("", analyzer, anonymizer, pii_enabled=True)

        analyzer.analyze.assert_not_called()


# ---------------------------------------------------------------------------
# mask_text — no PII detected
# ---------------------------------------------------------------------------

class TestMaskTextNoPii:
    """When analyzer returns no results, original text is returned unchanged."""

    def test_no_results_returns_original_text(self):
        analyzer, anonymizer = _make_engines(analyze_return=[])
        text = "Hello, how are you today?"
        result_text, entities = mask_text(text, analyzer, anonymizer, pii_enabled=True)

        assert result_text == text

    def test_no_results_returns_empty_entity_list(self):
        analyzer, anonymizer = _make_engines(analyze_return=[])
        _, entities = mask_text("Hello world", analyzer, anonymizer, pii_enabled=True)

        assert entities == []

    def test_anonymizer_not_called_when_no_results(self):
        analyzer, anonymizer = _make_engines(analyze_return=[])
        mask_text("Hello world", analyzer, anonymizer, pii_enabled=True)

        anonymizer.anonymize.assert_not_called()


# ---------------------------------------------------------------------------
# mask_text — email detected and masked
# ---------------------------------------------------------------------------

class TestMaskTextEmailDetected:
    """Text containing an email address is masked to [REDACTED_EMAIL_ADDRESS]."""

    def test_email_text_is_replaced(self):
        email_result = _make_analyzer_result("EMAIL_ADDRESS", start=12, end=28)
        masked_text = "My email is [REDACTED_EMAIL_ADDRESS]"
        analyzer, anonymizer = _make_engines(
            analyze_return=[email_result],
            anonymize_text=masked_text,
        )

        result_text, _ = mask_text(
            "My email is user@example.com", analyzer, anonymizer, pii_enabled=True
        )

        assert result_text == masked_text

    def test_email_entity_type_in_result(self):
        email_result = _make_analyzer_result("EMAIL_ADDRESS", start=12, end=28)
        analyzer, anonymizer = _make_engines(
            analyze_return=[email_result],
            anonymize_text="My email is [REDACTED_EMAIL_ADDRESS]",
        )

        _, entities = mask_text(
            "My email is user@example.com", analyzer, anonymizer, pii_enabled=True
        )

        assert "EMAIL_ADDRESS" in entities

    def test_analyzer_called_with_correct_arguments(self):
        email_result = _make_analyzer_result("EMAIL_ADDRESS", start=0, end=16)
        analyzer, anonymizer = _make_engines(
            analyze_return=[email_result],
            anonymize_text="[REDACTED_EMAIL_ADDRESS]",
        )
        text = "user@example.com"

        mask_text(text, analyzer, anonymizer, pii_enabled=True)

        analyzer.analyze.assert_called_once_with(
            text=text,
            entities=POC_ENTITIES,
            language="en",
            score_threshold=MIN_CONFIDENCE,
        )

    def test_phone_number_entity_type_in_result(self):
        phone_result = _make_analyzer_result("PHONE_NUMBER", start=0, end=12)
        analyzer, anonymizer = _make_engines(
            analyze_return=[phone_result],
            anonymize_text="[REDACTED_PHONE_NUMBER]",
        )

        _, entities = mask_text(
            "555-867-5309", analyzer, anonymizer, pii_enabled=True
        )

        assert "PHONE_NUMBER" in entities

    def test_person_entity_type_in_result(self):
        person_result = _make_analyzer_result("PERSON", start=0, end=10)
        analyzer, anonymizer = _make_engines(
            analyze_return=[person_result],
            anonymize_text="[REDACTED_PERSON]",
        )

        _, entities = mask_text(
            "John Smith called.", analyzer, anonymizer, pii_enabled=True
        )

        assert "PERSON" in entities


# ---------------------------------------------------------------------------
# mask_text — entity type deduplication
# ---------------------------------------------------------------------------

class TestMaskTextDeduplication:
    """When multiple results share the same entity type, it appears once."""

    def test_duplicate_entity_types_deduplicated(self):
        # Two EMAIL_ADDRESS results (e.g. two emails in the same text)
        result1 = _make_analyzer_result("EMAIL_ADDRESS", start=0, end=16)
        result2 = _make_analyzer_result("EMAIL_ADDRESS", start=20, end=36)
        analyzer, anonymizer = _make_engines(
            analyze_return=[result1, result2],
            anonymize_text="[REDACTED_EMAIL_ADDRESS] and [REDACTED_EMAIL_ADDRESS]",
        )

        _, entities = mask_text(
            "a@b.com and c@d.com", analyzer, anonymizer, pii_enabled=True
        )

        assert entities.count("EMAIL_ADDRESS") == 1

    def test_multiple_entity_types_all_present(self):
        email_result = _make_analyzer_result("EMAIL_ADDRESS", start=0, end=10)
        phone_result = _make_analyzer_result("PHONE_NUMBER", start=15, end=27)
        analyzer, anonymizer = _make_engines(
            analyze_return=[email_result, phone_result],
            anonymize_text="[REDACTED_EMAIL_ADDRESS] [REDACTED_PHONE_NUMBER]",
        )

        _, entities = mask_text(
            "a@b.com and 555-555-5555", analyzer, anonymizer, pii_enabled=True
        )

        assert "EMAIL_ADDRESS" in entities
        assert "PHONE_NUMBER" in entities

    def test_entity_list_has_no_duplicates(self):
        results = [
            _make_analyzer_result("EMAIL_ADDRESS", 0, 10),
            _make_analyzer_result("EMAIL_ADDRESS", 15, 25),
            _make_analyzer_result("PERSON", 30, 40),
            _make_analyzer_result("PERSON", 45, 55),
        ]
        analyzer, anonymizer = _make_engines(
            analyze_return=results,
            anonymize_text="masked text",
        )

        _, entities = mask_text("some text", analyzer, anonymizer, pii_enabled=True)

        assert len(entities) == len(set(entities)), "Entity list must have no duplicates"


# ---------------------------------------------------------------------------
# mask_messages — all messages processed
# ---------------------------------------------------------------------------

class TestMaskMessages:
    """mask_messages applies mask_text to each message and aggregates results."""

    def test_pii_disabled_returns_messages_unchanged(self):
        analyzer, anonymizer = _make_engines()
        messages = [
            {"role": "user", "content": "user@example.com"},
            {"role": "assistant", "content": "Hello John"},
        ]

        updated, entities = mask_messages(messages, analyzer, anonymizer, pii_enabled=False)

        assert updated[0]["content"] == "user@example.com"
        assert updated[1]["content"] == "Hello John"
        assert entities == []

    def test_all_messages_are_processed(self):
        """Each message's content field must be passed through mask_text."""
        # First message has an email, second has a phone number.
        email_result = _make_analyzer_result("EMAIL_ADDRESS", 0, 16)
        phone_result = _make_analyzer_result("PHONE_NUMBER", 0, 12)

        call_count = 0
        original_contents = ["user@example.com", "555-867-5309"]
        masked_contents = ["[REDACTED_EMAIL_ADDRESS]", "[REDACTED_PHONE_NUMBER]"]

        analyzer = MagicMock()
        anonymizer = MagicMock()

        def analyze_side_effect(text, **kwargs):
            nonlocal call_count
            if text == original_contents[0]:
                return [email_result]
            elif text == original_contents[1]:
                return [phone_result]
            return []

        def anonymize_side_effect(text, analyzer_results, operators):
            if text == original_contents[0]:
                return _make_anonymized(masked_contents[0])
            elif text == original_contents[1]:
                return _make_anonymized(masked_contents[1])
            return _make_anonymized(text)

        analyzer.analyze.side_effect = analyze_side_effect
        anonymizer.anonymize.side_effect = anonymize_side_effect

        messages = [
            {"role": "user", "content": original_contents[0]},
            {"role": "user", "content": original_contents[1]},
        ]

        updated, entities = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert updated[0]["content"] == masked_contents[0]
        assert updated[1]["content"] == masked_contents[1]

    def test_entity_types_aggregated_across_messages(self):
        """Entity types from all messages are combined into a single list."""
        email_result = _make_analyzer_result("EMAIL_ADDRESS", 0, 16)
        phone_result = _make_analyzer_result("PHONE_NUMBER", 0, 12)

        analyzer = MagicMock()
        anonymizer = MagicMock()

        def analyze_side_effect(text, **kwargs):
            if "email" in text.lower() or "@" in text:
                return [email_result]
            if "555" in text:
                return [phone_result]
            return []

        analyzer.analyze.side_effect = analyze_side_effect
        anonymizer.anonymize.return_value = _make_anonymized("[REDACTED]")

        messages = [
            {"role": "user", "content": "user@example.com"},
            {"role": "user", "content": "555-867-5309"},
        ]

        _, entities = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert "EMAIL_ADDRESS" in entities
        assert "PHONE_NUMBER" in entities

    def test_aggregated_entity_types_deduplicated(self):
        """Same entity type found in multiple messages appears only once."""
        email_result = _make_analyzer_result("EMAIL_ADDRESS", 0, 16)

        analyzer = MagicMock()
        anonymizer = MagicMock()
        analyzer.analyze.return_value = [email_result]
        anonymizer.anonymize.return_value = _make_anonymized("[REDACTED_EMAIL_ADDRESS]")

        messages = [
            {"role": "user", "content": "a@b.com"},
            {"role": "user", "content": "c@d.com"},
        ]

        _, entities = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert entities.count("EMAIL_ADDRESS") == 1

    def test_message_list_length_preserved(self):
        """Output list has the same number of messages as the input."""
        analyzer, anonymizer = _make_engines(analyze_return=[])
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
            {"role": "user", "content": "foo bar"},
        ]

        updated, _ = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert len(updated) == len(messages)

    def test_non_content_fields_preserved(self):
        """Other message fields (role, etc.) are not modified."""
        analyzer, anonymizer = _make_engines(analyze_return=[])
        messages = [{"role": "user", "content": "hello world"}]

        updated, _ = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert updated[0]["role"] == "user"

    def test_empty_messages_list_returns_empty(self):
        analyzer, anonymizer = _make_engines()

        updated, entities = mask_messages([], analyzer, anonymizer, pii_enabled=True)

        assert updated == []
        assert entities == []

    def test_no_pii_across_all_messages_returns_empty_entity_list(self):
        analyzer, anonymizer = _make_engines(analyze_return=[])
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        _, entities = mask_messages(messages, analyzer, anonymizer, pii_enabled=True)

        assert entities == []
