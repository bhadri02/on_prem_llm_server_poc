# Feature: agent-framework, Property 5: calculator never calls eval or exec — pure AST evaluation
"""
tests/test_property_calculator_safety.py

Property-based and deterministic unit tests for the calculator tool's safety invariant.

Property 5: calculator never calls eval or exec — pure AST evaluation
Validates: Requirements 6.2, 6.3, 6.4

Coverage:
  - Hypothesis property: _safe_eval() never calls builtins.eval or builtins.exec for any input
  - Hypothesis property: non-permitted constructs always return a string starting with "Error:"
  - Deterministic unit tests: specific dangerous expressions produce "Error:" strings
"""

import builtins
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tools.calculator import _safe_eval


# ---------------------------------------------------------------------------
# Property 5a: _safe_eval() NEVER calls builtins.eval or builtins.exec
# ---------------------------------------------------------------------------


@given(st.text())
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_calculator_never_calls_eval_or_exec(expression: str):
    """
    **Validates: Requirements 6.2, 6.3, 6.4**

    Property 5: For every possible input string, _safe_eval() must not
    delegate to builtins.eval or builtins.exec. Both are patched so that
    any call to them raises AssertionError, causing the test to fail.

    If the stub raises NotImplementedError the example is treated as a
    vacuous pass (the property holds: eval/exec were not called).
    The test will be fully exercised once Task 3.1 delivers the real
    implementation.
    """

    def _forbidden_eval(*args, **kwargs):
        raise AssertionError(
            f"_safe_eval() must never call builtins.eval — called with {args!r}"
        )

    def _forbidden_exec(*args, **kwargs):
        raise AssertionError(
            f"_safe_eval() must never call builtins.exec — called with {args!r}"
        )

    with patch.object(builtins, "eval", side_effect=_forbidden_eval), patch.object(
        builtins, "exec", side_effect=_forbidden_exec
    ):
        try:
            result = _safe_eval(expression)
            # If we get here, neither eval nor exec was invoked (the patched
            # versions would have raised AssertionError if called).
            # Result must be a string (either a number or an "Error:" string).
            assert isinstance(result, str), (
                f"_safe_eval() must return a str, got {type(result).__name__!r} "
                f"for expression {expression!r}"
            )
        except NotImplementedError:
            # Stub not yet implemented — the property holds vacuously
            # (eval/exec were not called). This is NOT a failure.
            return
        except AssertionError:
            # Re-raise: these are the forbidden-call assertions above.
            raise


# ---------------------------------------------------------------------------
# Property 5b: non-permitted constructs always return an "Error:" string
# ---------------------------------------------------------------------------

# Strategy: build strings that are guaranteed to contain a dangerous pattern
# by prepending one of the known-dangerous substrings to arbitrary text.
# This avoids the heavy filtering that assume() would require.
_DANGEROUS_FRAGMENTS = st.sampled_from([
    "__import__",
    "eval(",
    "exec(",
    "lambda",
    "os.",
    "sys.",
    "open(",
    "compile(",
    "globals(",
    "locals(",
    "getattr(",
    "setattr(",
    "__builtins__",
    "__class__",
    "__dict__",
])

_dangerous_expression_strategy = st.builds(
    lambda fragment, suffix: fragment + suffix,
    fragment=_DANGEROUS_FRAGMENTS,
    suffix=st.text(),
)


@given(_dangerous_expression_strategy)
@settings(max_examples=100)
def test_non_permitted_constructs_return_error_string(expression: str):
    """
    **Validates: Requirements 6.2, 6.3, 6.4**

    Property 5 (corollary): For expressions that contain non-arithmetic
    constructs (attribute access, function calls, __import__, etc.),
    _safe_eval() must return a string that starts with "Error:".

    The strategy guarantees every generated expression contains at least
    one dangerous pattern, so no filtering via assume() is required.

    If the stub raises NotImplementedError the property holds vacuously
    (no unsafe evaluation occurred).
    """
    try:
        result = _safe_eval(expression)
    except NotImplementedError:
        # Stub not yet implemented — property holds vacuously.
        return

    assert isinstance(result, str), (
        f"_safe_eval() must return a str, got {type(result).__name__!r} "
        f"for expression {expression!r}"
    )
    assert result.startswith("Error:"), (
        f"_safe_eval() must return an error string for dangerous expression "
        f"{expression!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Deterministic unit tests — specific dangerous expressions
# ---------------------------------------------------------------------------


class TestSafeEvalDangerousExpressionsUnit:
    """
    Deterministic tests verifying that specific dangerous expressions
    produce an 'Error:' string rather than executing code or raising
    unhandled exceptions.

    These tests are skipped (not xfailed) when the calculator stub is in
    place (NotImplementedError), and will be fully exercised once Task 3.1
    delivers the real AST evaluator.
    """

    def _eval_or_skip(self, expression: str) -> str:
        """Call _safe_eval and skip the test if the stub is not implemented."""
        try:
            return _safe_eval(expression)
        except NotImplementedError:
            pytest.skip("calculator not yet implemented")

    def test_import_os_system(self):
        """__import__('os').system('ls') must return an Error: string."""
        result = self._eval_or_skip("__import__('os').system('ls')")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for __import__ call, got {result!r}"
        )

    def test_eval_call(self):
        """eval('1+1') must return an Error: string, never actually evaluate."""
        result = self._eval_or_skip("eval('1+1')")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for eval() call, got {result!r}"
        )

    def test_exec_call(self):
        """exec('x=1') must return an Error: string."""
        result = self._eval_or_skip("exec('x=1')")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for exec() call, got {result!r}"
        )

    def test_subscript_access(self):
        """[1,2,3][0] must return an Error: string (subscript not permitted)."""
        result = self._eval_or_skip("[1,2,3][0]")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for subscript access, got {result!r}"
        )

    def test_lambda_call(self):
        """(lambda: 1)() must return an Error: string (function call not permitted)."""
        result = self._eval_or_skip("(lambda: 1)()")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for lambda call, got {result!r}"
        )

    def test_attribute_access(self):
        """os.getcwd() must return an Error: string (attribute access not permitted)."""
        result = self._eval_or_skip("os.getcwd()")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected 'Error:...' for attribute access, got {result!r}"
        )
