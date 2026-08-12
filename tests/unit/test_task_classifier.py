"""
tests/unit/test_task_classifier.py

Unit tests for intelligent_router.task_classifier.

Covers:
  - load_classifier_rules: YAML not found → None + ERROR log
  - load_classifier_rules: malformed YAML → None + ERROR log
  - load_classifier_rules: empty YAML file (parses to None) → None + ERROR log
  - load_classifier_rules: empty rules map in valid YAML → ClassifierRules(rules={})
  - load_classifier_rules: valid file → correct ClassifierRules populated
  - ClassifierRules.total_keyword_count: sums across all task types
  - classify_task: priority order — code beats reasoning when both keywords present
  - classify_task: default "chat" returned when no keyword matches
  - classify_task: classification is case-insensitive
  - classify_task: multi-message content is concatenated correctly
  - classify_task: None/missing content fields are treated as empty strings
  - classify_task: empty messages list returns default

Note on log capture:
  get_logger() attaches a StreamHandler(sys.stdout) with propagate=False so
  pytest's caplog (which hooks the root logger via propagation) cannot intercept
  those records.  The three log-verification tests use _capture_error_logs(),
  which temporarily attaches a StringIO handler directly to the module logger.
"""

import io
import logging
import textwrap

import pytest

from intelligent_router.task_classifier import (
    PRIORITY_ORDER,
    ClassifierRules,
    classify_task,
    load_classifier_rules,
)
# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_rules(
    code=None,
    reasoning=None,
    summarization=None,
    translation=None,
    chat=None,
    default="chat",
) -> ClassifierRules:
    """Build a ClassifierRules with only the supplied task types populated."""
    rules: dict[str, list[str]] = {}
    if code is not None:
        rules["code"] = code
    if reasoning is not None:
        rules["reasoning"] = reasoning
    if summarization is not None:
        rules["summarization"] = summarization
    if translation is not None:
        rules["translation"] = translation
    if chat is not None:
        rules["chat"] = chat
    return ClassifierRules(rules=rules, default=default)


def _msg(content) -> dict:
    """Convenience: build a minimal message dict."""
    return {"role": "user", "content": content}


# ---------------------------------------------------------------------------
# Helpers — capture ERROR messages from the task_classifier logger
# ---------------------------------------------------------------------------


def _capture_error_logs(fn):
    """
    Call fn() and return a list of ERROR-level 'message' strings emitted
    by the intelligent_router.task_classifier logger.

    get_logger() stores a StreamHandler pointing at sys.stdout at import
    time, before pytest replaces sys.stdout per test.  We bypass this by
    temporarily attaching a StringIO handler directly to the logger.
    """
    import intelligent_router.task_classifier as _mod
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _mod.logger
    logger.addHandler(handler)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
    lines = buf.getvalue().splitlines()
    return [line for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# load_classifier_rules — file not found
# ---------------------------------------------------------------------------

class TestLoadClassifierRulesFileNotFound:
    def test_returns_none_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "no_such_file.yaml")
        result = load_classifier_rules(missing)
        assert result is None

    def test_logs_error_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "no_such_file.yaml")
        error_messages = _capture_error_logs(lambda: load_classifier_rules(missing))
        assert any("not found" in m.lower() or "no_such_file" in m for m in error_messages), (
            f"Expected a 'not found' ERROR log, got: {error_messages}"
        )


# ---------------------------------------------------------------------------
# load_classifier_rules — malformed YAML
# ---------------------------------------------------------------------------

class TestLoadClassifierRulesMalformedYaml:
    def test_returns_none_on_malformed_yaml(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        # Deliberately invalid YAML (unmatched bracket triggers YAMLError)
        bad_yaml.write_text("rules: [\ndefault: chat", encoding="utf-8")
        result = load_classifier_rules(str(bad_yaml))
        assert result is None

    def test_logs_error_on_malformed_yaml(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("rules: [\ndefault: chat", encoding="utf-8")
        error_messages = _capture_error_logs(lambda: load_classifier_rules(str(bad_yaml)))
        assert any("malformed" in m.lower() or "yaml" in m.lower() for m in error_messages), (
            f"Expected a 'malformed YAML' ERROR log, got: {error_messages}"
        )


# ---------------------------------------------------------------------------
# load_classifier_rules — empty YAML (parses to None) is an error
# ---------------------------------------------------------------------------

class TestLoadClassifierRulesEmptyYaml:
    def test_empty_yaml_file_returns_none(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        result = load_classifier_rules(str(empty))
        assert result is None

    def test_empty_yaml_file_logs_error(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        error_messages = _capture_error_logs(lambda: load_classifier_rules(str(empty)))
        assert error_messages, f"Expected at least one ERROR log, got none"


# ---------------------------------------------------------------------------
# load_classifier_rules — empty rules map is valid
# ---------------------------------------------------------------------------

class TestLoadClassifierRulesEmptyRulesMap:
    def test_empty_rules_map_returns_classifier_rules(self, tmp_path):
        yaml_file = tmp_path / "empty_rules.yaml"
        yaml_file.write_text("rules: {}\ndefault: chat\n", encoding="utf-8")
        result = load_classifier_rules(str(yaml_file))
        assert result is not None, "Empty rules map should return ClassifierRules, not None"
        assert isinstance(result, ClassifierRules)
        assert result.rules == {}
        assert result.default == "chat"

    def test_empty_rules_map_total_keyword_count_is_zero(self, tmp_path):
        yaml_file = tmp_path / "empty_rules.yaml"
        yaml_file.write_text("rules: {}\ndefault: chat\n", encoding="utf-8")
        result = load_classifier_rules(str(yaml_file))
        assert result is not None
        assert result.total_keyword_count == 0


# ---------------------------------------------------------------------------
# load_classifier_rules — valid YAML is loaded correctly
# ---------------------------------------------------------------------------

class TestLoadClassifierRulesValidYaml:
    YAML_CONTENT = textwrap.dedent("""\
        rules:
          code:
            - python
            - javascript
            - function
          reasoning:
            - analyze
            - why
          summarization:
            - summarize
          translation:
            - translate
        default: chat
    """)

    def test_returns_classifier_rules_instance(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(self.YAML_CONTENT, encoding="utf-8")
        result = load_classifier_rules(str(f))
        assert isinstance(result, ClassifierRules)

    def test_rules_populated_correctly(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(self.YAML_CONTENT, encoding="utf-8")
        result = load_classifier_rules(str(f))
        assert result is not None
        assert result.rules["code"] == ["python", "javascript", "function"]
        assert result.rules["reasoning"] == ["analyze", "why"]

    def test_default_populated_correctly(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(self.YAML_CONTENT, encoding="utf-8")
        result = load_classifier_rules(str(f))
        assert result is not None
        assert result.default == "chat"

    def test_total_keyword_count(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(self.YAML_CONTENT, encoding="utf-8")
        result = load_classifier_rules(str(f))
        assert result is not None
        # code=3, reasoning=2, summarization=1, translation=1 → 7
        assert result.total_keyword_count == 7

    def test_missing_default_key_falls_back_to_chat(self, tmp_path):
        yaml_content = "rules:\n  code:\n    - python\n"
        f = tmp_path / "rules_no_default.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        result = load_classifier_rules(str(f))
        assert result is not None
        assert result.default == "chat"


# ---------------------------------------------------------------------------
# ClassifierRules.total_keyword_count
# ---------------------------------------------------------------------------

class TestTotalKeywordCount:
    def test_empty_rules(self):
        rules = ClassifierRules(rules={})
        assert rules.total_keyword_count == 0

    def test_single_task_type(self):
        rules = ClassifierRules(rules={"code": ["python", "function", "debug"]})
        assert rules.total_keyword_count == 3

    def test_multiple_task_types(self):
        rules = _make_rules(
            code=["python", "javascript"],
            reasoning=["analyze"],
            summarization=["summarize", "tldr", "recap"],
        )
        assert rules.total_keyword_count == 6


# ---------------------------------------------------------------------------
# classify_task — priority order
# ---------------------------------------------------------------------------

class TestClassifyTaskPriorityOrder:
    def test_priority_order_is_correct(self):
        assert PRIORITY_ORDER == ["code", "reasoning", "summarization", "translation", "chat"]

    def test_code_beats_reasoning_when_both_keywords_present(self):
        """When both a code keyword and a reasoning keyword appear, code wins."""
        rules = _make_rules(
            code=["function"],
            reasoning=["analyze"],
        )
        messages = [_msg("Please analyze this function for me")]
        assert classify_task(messages, rules) == "code"

    def test_code_beats_summarization(self):
        rules = _make_rules(code=["python"], summarization=["summarize"])
        messages = [_msg("Can you summarize this python script")]
        assert classify_task(messages, rules) == "code"

    def test_reasoning_beats_summarization(self):
        rules = _make_rules(reasoning=["why"], summarization=["summarize"])
        messages = [_msg("Summarize and explain why this works")]
        assert classify_task(messages, rules) == "reasoning"

    def test_summarization_beats_translation(self):
        rules = _make_rules(summarization=["summarize"], translation=["translate"])
        messages = [_msg("Please summarize and translate this text")]
        assert classify_task(messages, rules) == "summarization"

    def test_translation_beats_chat_keyword(self):
        rules = _make_rules(translation=["translate"], chat=["hello"])
        messages = [_msg("Hello, please translate this")]
        assert classify_task(messages, rules) == "translation"

    def test_only_highest_priority_match_returned(self):
        """All task types have a keyword in the message; only code is returned."""
        rules = _make_rules(
            code=["code"],
            reasoning=["reason"],
            summarization=["summary"],
            translation=["translate"],
        )
        messages = [_msg("code reason summary translate")]
        assert classify_task(messages, rules) == "code"


# ---------------------------------------------------------------------------
# classify_task — default on no match
# ---------------------------------------------------------------------------

class TestClassifyTaskDefault:
    def test_returns_default_chat_when_no_match(self):
        rules = _make_rules(code=["python"])
        messages = [_msg("What is the weather today?")]
        assert classify_task(messages, rules) == "chat"

    def test_returns_custom_default(self):
        rules = ClassifierRules(rules={"code": ["python"]}, default="custom_default")
        messages = [_msg("What is the weather today?")]
        assert classify_task(messages, rules) == "custom_default"

    def test_empty_messages_list_returns_default(self):
        rules = _make_rules(code=["python"])
        assert classify_task([], rules) == "chat"

    def test_empty_rules_always_returns_default(self):
        rules = ClassifierRules(rules={})
        messages = [_msg("Write me a python script")]
        assert classify_task(messages, rules) == "chat"


# ---------------------------------------------------------------------------
# classify_task — case insensitivity
# ---------------------------------------------------------------------------

class TestClassifyTaskCaseInsensitive:
    def test_uppercase_content_matches_lowercase_keyword(self):
        rules = _make_rules(code=["python"])
        messages = [_msg("PYTHON is great")]
        assert classify_task(messages, rules) == "code"

    def test_mixed_case_content_matches(self):
        rules = _make_rules(code=["python"])
        messages = [_msg("PyThOn rocks")]
        assert classify_task(messages, rules) == "code"

    def test_uppercase_keyword_matches_lowercase_content(self):
        rules = _make_rules(code=["PYTHON"])
        messages = [_msg("python is great")]
        assert classify_task(messages, rules) == "code"

    def test_titlecase_keyword_matches(self):
        rules = _make_rules(reasoning=["Analyze"])
        messages = [_msg("please analyze this")]
        assert classify_task(messages, rules) == "reasoning"

    def test_partial_keyword_match_is_case_insensitive(self):
        """Substring match should also be case-insensitive."""
        rules = _make_rules(summarization=["summarize"])
        messages = [_msg("SUMMARIZE this document")]
        assert classify_task(messages, rules) == "summarization"


# ---------------------------------------------------------------------------
# classify_task — multi-message concatenation
# ---------------------------------------------------------------------------

class TestClassifyTaskMultiMessageConcatenation:
    def test_keyword_in_second_message_is_found(self):
        rules = _make_rules(code=["python"])
        messages = [
            _msg("Hello"),
            _msg("Write a python script"),
        ]
        assert classify_task(messages, rules) == "code"

    def test_keyword_spanning_message_boundary_is_not_matched(self):
        """'py' in message 1 and 'thon' in message 2 should NOT match 'python'."""
        rules = _make_rules(code=["python"])
        messages = [
            _msg("py"),
            _msg("thon"),
        ]
        # Concatenation is "py thon" (space-separated), 'python' not in "py thon"
        assert classify_task(messages, rules) == "chat"

    def test_all_messages_concatenated_with_space(self):
        """Verify the space separator is actually used."""
        rules = _make_rules(code=["write a script"])
        messages = [
            _msg("write a"),
            _msg("script please"),
        ]
        # "write a script" spans the space-joined text → should match
        assert classify_task(messages, rules) == "code"

    def test_none_content_treated_as_empty_string(self):
        rules = _make_rules(code=["python"])
        messages = [
            {"role": "user", "content": None},
            _msg("python"),
        ]
        assert classify_task(messages, rules) == "code"

    def test_missing_content_key_treated_as_empty_string(self):
        rules = _make_rules(code=["python"])
        messages = [
            {"role": "system"},  # no "content" key
            _msg("python"),
        ]
        assert classify_task(messages, rules) == "code"

    def test_all_none_content_returns_default(self):
        rules = _make_rules(code=["python"])
        messages = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": None},
        ]
        assert classify_task(messages, rules) == "chat"

    def test_three_messages_first_keyword_wins_by_priority(self):
        rules = _make_rules(
            code=["function"],
            reasoning=["analyze"],
            summarization=["summarize"],
        )
        messages = [
            _msg("Please summarize"),
            _msg("and analyze"),
            _msg("this function"),
        ]
        assert classify_task(messages, rules) == "code"


class TestClassifyTaskHarnessWrapperExclusion:
    """Real bug regression: GitHub Copilot Chat's agent-mode harness appends
    a near-constant <context>/<reminderinstructions> tool block to every
    request, full of code-editing language (e.g. "insert_edit_into_file").
    Before this fix, classify_task's default keyword rules (which include
    "insert_edit_into_file"-adjacent terms as "code" keywords, "code" being
    first in PRIORITY_ORDER) matched this wrapper text on every single
    request from this client, misclassifying "tell me a joke" as
    task_type="code" — this affects RBAC policy enforcement
    (intelligent_router/pipeline.py Stage 2b's (role, task_type) check runs
    regardless of whether the model was pinned or auto-selected), not just
    model auto-selection.
    """

    _WRAPPER = (
        "<context>\nthe current date is 2026-08-12.\n</context>\n"
        "<reminderinstructions>\nwhen using the insert_edit_into_file tool, "
        "avoid repeating existing code.\n</reminderinstructions>"
    )

    def test_wrapper_message_excluded_from_classification(self):
        rules = _make_rules(code=["insert_edit_into_file", "repeating existing code"])
        messages = [
            _msg("tell me a joke"),
            _msg(self._WRAPPER),
        ]
        assert classify_task(messages, rules) == "chat"

    def test_real_question_still_classified_normally_alongside_wrapper(self):
        rules = _make_rules(code=["insert_edit_into_file", "python"])
        messages = [
            _msg("write me a python function"),
            _msg(self._WRAPPER),
        ]
        assert classify_task(messages, rules) == "code"

    def test_wrapper_prefix_matching_is_case_insensitive(self):
        rules = _make_rules(code=["insert_edit_into_file"])
        messages = [
            _msg("tell me a joke"),
            _msg(self._WRAPPER.upper()),
        ]
        assert classify_task(messages, rules) == "chat"
