"""
Property-based tests for the Security & Governance Layer.

Properties covered:

- Property 1a: scan_for_injection with empty pattern list always returns 0.0
- Property 1b: scan_for_injection always returns 1.0 when a known pattern is
               embedded in the message (regardless of surrounding text)
- Property 6:  scan_for_injection is deterministic — same inputs always
               produce the same output
- Property 7:  check_policy denies when roles is None, empty, or contains
               only unauthorised role strings
- Property 7b: check_policy passes when roles contains at least one of the
               three authorised role strings
- Property 8:  check_content_safety returns False whenever a blocklisted
               word appears in the messages (regardless of surrounding text)
- Property 8b: check_content_safety returns True for content that contains
               none of the blocklisted words

**Validates: Requirements 1, 6, 7, 8**
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import os
import re

# ---------------------------------------------------------------------------
# Set required env vars BEFORE importing any security_layer module, so that
# the module-level ``settings = Settings()`` instantiation in config.py
# succeeds without raising a ValidationError.
# ---------------------------------------------------------------------------
_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
    "PII_ENABLED": "false",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register the 'ci' Hypothesis settings profile.
# Must be done before any @given-decorated functions are defined.
# ---------------------------------------------------------------------------
h_settings.register_profile("ci", max_examples=100)
h_settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from security_layer.content_safety import BLOCKLIST, check_content_safety
from security_layer.injection import scan_for_injection
from security_layer.policy import check_policy


# ===========================================================================
# Property 1a — empty pattern list always returns 0.0
# ===========================================================================


@h_settings(max_examples=100)
@given(
    messages=st.lists(
        st.fixed_dictionaries({
            "role": st.sampled_from(["user", "assistant", "system"]),
            "content": st.text(min_size=1),
        }),
        min_size=1,
        max_size=5,
    )
)
def test_injection_no_match_gives_zero_score(messages):
    """**Validates: Requirements 1**

    For any list of messages and an empty compiled-pattern list,
    scan_for_injection SHALL return exactly 0.0.
    """
    assert scan_for_injection(messages, []) == 0.0


# ===========================================================================
# Property 1b — known pattern always gives 1.0
# ===========================================================================


@h_settings(max_examples=100)
@given(
    prefix=st.text(),
    suffix=st.text(),
    pattern_str=st.sampled_from([
        "ignore previous instructions",
        "you are now",
        "pretend you are",
    ]),
)
def test_injection_match_gives_one_score(prefix, suffix, pattern_str):
    """**Validates: Requirements 1**

    For any prefix and suffix text, when a known injection phrase is embedded
    inside the message content, scan_for_injection SHALL return 1.0.
    """
    messages = [{"role": "user", "content": prefix + pattern_str + suffix}]
    compiled = [re.compile(pattern_str, re.IGNORECASE)]
    assert scan_for_injection(messages, compiled) == 1.0


# ===========================================================================
# Property 6 — determinism
# ===========================================================================


@h_settings(max_examples=100)
@given(
    messages=st.lists(
        st.fixed_dictionaries({
            "role": st.sampled_from(["user", "assistant", "system"]),
            "content": st.text(),
        }),
        min_size=0,
        max_size=5,
    ),
    pattern_strings=st.lists(
        st.sampled_from([
            "ignore previous instructions",
            "you are now",
            "pretend you are",
            "disregard your",
            "forget your training",
        ]),
        min_size=0,
        max_size=5,
    ),
)
def test_injection_determinism(messages, pattern_strings):
    """**Validates: Requirements 6**

    For any combination of messages and patterns, calling scan_for_injection
    twice with identical inputs SHALL return the same value both times.
    """
    patterns = [re.compile(p, re.IGNORECASE) for p in set(pattern_strings)]
    result1 = scan_for_injection(messages, patterns)
    result2 = scan_for_injection(messages, patterns)
    assert result1 == result2


# ===========================================================================
# Property 7 — policy denies for unauthorised roles
# ===========================================================================


@h_settings(max_examples=100)
@given(
    roles=st.one_of(
        st.none(),
        st.just([]),
        st.lists(
            st.text().filter(lambda r: r not in {"developer", "analyst", "admin"}),
            min_size=1,
            max_size=5,
        ),
    )
)
def test_policy_deny_for_unauthorized_roles(roles):
    """**Validates: Requirements 7**

    For any roles value that is None, an empty list, or a list containing
    only strings that are NOT one of the three authorised roles, check_policy
    SHALL return (False, "role_check_deny").
    """
    assert check_policy(roles) == (False, "role_check_deny")


# ===========================================================================
# Property 7b — policy passes when at least one authorised role is present
# ===========================================================================


@h_settings(max_examples=100)
@given(
    valid_role=st.sampled_from(["developer", "analyst", "admin"]),
    extra=st.lists(st.text(), max_size=3),
)
def test_policy_pass_for_any_authorized_role(valid_role, extra):
    """**Validates: Requirements 7**

    For any list that includes at least one of the three authorised role
    strings ("developer", "analyst", "admin"), check_policy SHALL return a
    tuple whose first element is True.
    """
    result = check_policy([valid_role] + extra)
    assert result[0] is True


# ===========================================================================
# Property 8 — blocklisted word always fails content safety
# ===========================================================================


@h_settings(max_examples=100)
@given(
    prefix=st.text(),
    suffix=st.text(),
    word=st.sampled_from(BLOCKLIST),
)
def test_content_safety_blocks_blocklisted_word(prefix, suffix, word):
    """**Validates: Requirements 8**

    For any prefix and suffix text, when a blocklisted word is embedded inside
    the message content, check_content_safety SHALL return False (unsafe).
    """
    messages = [{"role": "user", "content": prefix + word + suffix}]
    assert check_content_safety(messages, BLOCKLIST) is False


# ===========================================================================
# Property 8b — clean prompt always passes content safety
# ===========================================================================


@h_settings(max_examples=100)
@given(
    content=st.text().filter(
        lambda t: not any(w.lower() in t.lower() for w in BLOCKLIST)
    ),
)
def test_content_safety_passes_clean_prompt(content):
    """**Validates: Requirements 8**

    For any text that does not contain any blocklisted word as a
    case-insensitive substring, check_content_safety SHALL return True (safe).
    """
    messages = [{"role": "user", "content": content}]
    assert check_content_safety(messages, BLOCKLIST) is True
