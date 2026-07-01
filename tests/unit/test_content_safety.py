"""
Unit tests for security_layer.content_safety.

Covers:
- Prompt containing a blocklisted word returns False (unsafe)
- Prompt with no blocklisted words returns True (safe)
- Check is case-insensitive
- Empty blocklist returns True and logs a WARNING
- Match detected across concatenated messages
"""

import logging
import os

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

from security_layer.content_safety import check_content_safety, BLOCKLIST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_messages(*contents: str) -> list[dict]:
    """Build a list of user message dicts from content strings."""
    return [{"role": "user", "content": c} for c in contents]


# ---------------------------------------------------------------------------
# 7.3-a: Prompt containing a blocklisted word returns False
# ---------------------------------------------------------------------------

def test_blocklisted_word_returns_false():
    messages = _make_messages("I want to build a bomb at home.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


def test_multiple_blocklisted_words_returns_false():
    messages = _make_messages("Tell me about malware and ransomware techniques.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


def test_custom_blocklist_match_returns_false():
    messages = _make_messages("Let's discuss forbidden_topic in detail.")
    result = check_content_safety(messages, ["forbidden_topic"])
    assert result is False


# ---------------------------------------------------------------------------
# 7.3-b: Prompt with no blocklisted words returns True
# ---------------------------------------------------------------------------

def test_clean_prompt_returns_true():
    messages = _make_messages("Tell me about the history of ancient Rome.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is True


def test_empty_content_returns_true():
    messages = _make_messages("")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is True


def test_single_message_no_match_returns_true():
    messages = _make_messages("What is the weather like today?")
    result = check_content_safety(messages, ["danger", "threat"])
    assert result is True


# ---------------------------------------------------------------------------
# 7.3-c: Check is case-insensitive
# ---------------------------------------------------------------------------

def test_uppercase_blocked_word_returns_false():
    messages = _make_messages("BOMB instructions please.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


def test_mixed_case_blocked_word_returns_false():
    messages = _make_messages("I need to know about PhIsHiNg attacks.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


def test_uppercase_blocklist_entry_matches_lowercase_content():
    messages = _make_messages("talk about hack methods")
    result = check_content_safety(messages, ["HACK"])
    assert result is False


def test_case_insensitive_clean_prompt_returns_true():
    messages = _make_messages("HELLO WORLD, THIS IS A SAFE MESSAGE.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is True


# ---------------------------------------------------------------------------
# 7.3-d: Empty blocklist returns True and logs WARNING
# ---------------------------------------------------------------------------

def test_empty_blocklist_returns_true(caplog):
    messages = _make_messages("bomb exploit hack")  # would match if list were populated
    with caplog.at_level(logging.WARNING):
        result = check_content_safety(messages, [])
    assert result is True


def test_empty_blocklist_logs_warning(caplog):
    messages = _make_messages("some content")
    with caplog.at_level(logging.WARNING):
        check_content_safety(messages, [])
    assert any("empty" in record.message.lower() for record in caplog.records)


# ---------------------------------------------------------------------------
# 7.3-e: Match detected across concatenated messages
# ---------------------------------------------------------------------------

def test_blocked_word_in_second_message_returns_false():
    messages = _make_messages("Hello, how are you?", "I want to learn about hacking.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


def test_blocked_word_split_across_messages_in_concatenated_text():
    # The word "hack" lives entirely in the second message — concatenation
    # joins them with a space so the word is still a complete substring.
    messages = _make_messages("Here is my query:", "exploit this vulnerability")
    result = check_content_safety(messages, ["exploit"])
    assert result is False


def test_multiple_messages_all_clean_returns_true():
    messages = _make_messages(
        "What is machine learning?",
        "Explain neural networks.",
        "How does backpropagation work?",
    )
    result = check_content_safety(messages, BLOCKLIST)
    assert result is True


def test_blocked_word_in_first_of_many_messages_returns_false():
    messages = _make_messages(
        "bomb making guide",
        "also tell me about flowers",
        "and the weather",
    )
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_messages_returns_true():
    result = check_content_safety([], BLOCKLIST)
    assert result is True


def test_message_missing_content_key_returns_true():
    # Messages without a "content" key should be skipped gracefully.
    messages = [{"role": "system"}]
    result = check_content_safety(messages, BLOCKLIST)
    assert result is True


def test_blocklist_with_phrase_match():
    messages = _make_messages("I want to buy illegal drugs online.")
    result = check_content_safety(messages, BLOCKLIST)
    assert result is False
