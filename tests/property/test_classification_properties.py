"""
Property-based tests for task classification in the Intelligent Router.

Properties covered:
  - Property 1: Task Classification — Keyword Match Invariant
    Whenever a message embeds a keyword for task type T (and no higher-priority
    keyword from a task type that comes before T in PRIORITY_ORDER is also
    present), classify_task returns T.

  - Property 2: Task Classification — Default Invariant
    Whenever ALL content fields contain no configured keyword,
    classify_task returns "chat" (the default task type).
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# Must be done before any @given-decorated function is defined.
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from intelligent_router.task_classifier import (
    PRIORITY_ORDER,
    ClassifierRules,
    classify_task,
)

# ---------------------------------------------------------------------------
# Shared test rules fixture (module-level constant)
# ---------------------------------------------------------------------------

# Representative keywords per task type — a subset of the full YAML file
# so we don't couple these tests to external file I/O.
RULES_DICT: dict[str, list[str]] = {
    "code": ["code", "function", "python", "javascript", "debug", "implement"],
    "reasoning": ["reason", "analyze", "logic", "deduce", "evaluate"],
    "summarization": ["summarize", "summary", "tldr", "brief", "condense"],
    "translation": ["translate", "translation", "in french", "in spanish"],
}

RULES = ClassifierRules(rules=RULES_DICT, default="chat")

# All keywords across all task types (used to filter them out in Property 2)
ALL_KEYWORDS: list[str] = [
    kw for kws in RULES_DICT.values() for kw in kws
]

# Task types that have keywords (everything except "chat" which is the default)
CLASSIFIABLE_TASK_TYPES = ["code", "reasoning", "summarization", "translation"]


def _rules_for_task(task_type: str) -> list[str]:
    """Return the keyword list for *task_type*."""
    return RULES_DICT[task_type]


def _has_higher_priority_keyword(text: str, task_type: str) -> bool:
    """Return True if *text* contains any keyword from a task type with HIGHER priority
    than *task_type* in PRIORITY_ORDER.
    """
    task_index = PRIORITY_ORDER.index(task_type)
    for higher_task in PRIORITY_ORDER[:task_index]:
        for kw in RULES_DICT.get(higher_task, []):
            if kw.lower() in text.lower():
                return True
    return False


# ---------------------------------------------------------------------------
# Property 1: Keyword Match Invariant
# ---------------------------------------------------------------------------

@given(
    prefix=st.text(max_size=50),
    suffix=st.text(max_size=50),
    task_type=st.sampled_from(CLASSIFIABLE_TASK_TYPES),
    keyword=st.data(),
    case_variant=st.sampled_from(["lower", "upper", "title"]),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_keyword_match_selects_highest_priority_task_type(
    prefix, suffix, task_type, keyword, case_variant
):
    """**Validates: Requirements 2.1, 2.3, 2.4**

    Property 1: Task Classification — Keyword Match Invariant.

    For any (prefix, keyword, suffix) combination where:
      - keyword belongs to task_type's keyword list, AND
      - neither prefix nor suffix contains a higher-priority keyword,

    classify_task(messages, RULES) MUST return task_type.
    """
    # Draw a keyword for the given task_type
    kw = keyword.draw(st.sampled_from(_rules_for_task(task_type)))

    # Apply case variant to the keyword
    if case_variant == "upper":
        kw_variant = kw.upper()
    elif case_variant == "title":
        kw_variant = kw.title()
    else:
        kw_variant = kw.lower()

    # Build the message content embedding the keyword between prefix and suffix
    content = f"{prefix} {kw_variant} {suffix}"

    # Skip examples where prefix or suffix happen to contain a higher-priority keyword
    assume(not _has_higher_priority_keyword(f"{prefix} {suffix}", task_type))

    messages = [{"role": "user", "content": content}]

    result = classify_task(messages, RULES)
    assert result == task_type, (
        f"Expected classify_task to return {task_type!r} for content {content!r}, "
        f"but got {result!r}. Keyword: {kw_variant!r}"
    )


# ---------------------------------------------------------------------------
# Property 2: Default Invariant
# ---------------------------------------------------------------------------

@given(
    messages=st.lists(
        st.fixed_dictionaries({
            "role": st.sampled_from(["user", "assistant", "system"]),
            "content": st.text().filter(
                lambda t: not any(kw.lower() in t.lower() for kw in ALL_KEYWORDS)
            ),
        }),
        min_size=0,
        max_size=5,
    )
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_no_keyword_match_always_returns_chat(messages):
    """**Validates: Requirements 2.2**

    Property 2: Task Classification — Default Invariant.

    When ALL content fields contain no configured keyword (keyword-free input),
    classify_task MUST return "chat" (the default task type) regardless of
    message count, role, or content text.

    This also covers the empty list case (min_size=0).
    """
    result = classify_task(messages, RULES)
    assert result == "chat", (
        f"Expected classify_task to return 'chat' for keyword-free messages, "
        f"but got {result!r}. Messages: {messages!r}"
    )


# ---------------------------------------------------------------------------
# Additional edge-case: empty messages list returns default
# ---------------------------------------------------------------------------

def test_empty_messages_returns_default():
    """classify_task with an empty messages list returns rules.default."""
    result = classify_task([], RULES)
    assert result == "chat"


# ---------------------------------------------------------------------------
# Additional edge-case: None content treated as empty string
# ---------------------------------------------------------------------------

def test_none_content_treated_as_empty():
    """classify_task with None content field does not raise and returns default."""
    messages = [{"role": "user", "content": None}]
    result = classify_task(messages, RULES)
    assert result == "chat"
