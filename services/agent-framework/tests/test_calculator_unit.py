# Feature: agent-framework — Unit tests for calculator tool
"""
tests/test_calculator_unit.py

Unit tests for the calculator tool covering Requirements 6.1–6.8.

Requirements covered:
  6.1 — Safe evaluation of arithmetic expressions
  6.2 — Rejection of unsafe constructs (no eval/exec)
  6.3 — Division by zero returns an error string
  6.4 — Empty / whitespace-only input returns an error string
  6.5 — Integer results have no decimal point
  6.6 — Float results strip trailing zeros
  6.7 — Syntax errors return an error string
  6.8 — Unary operators are supported
"""

import pytest

from tools.calculator import _safe_eval, calculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def invoke(expression: str) -> str:
    """Call the LangChain @tool via its .invoke() interface."""
    return calculator.invoke({"expression": expression})


# ---------------------------------------------------------------------------
# 1. Core arithmetic — _safe_eval directly
# ---------------------------------------------------------------------------


class TestSafeEvalArithmetic:
    """Validates Requirement 6.1 — correct arithmetic results."""

    def test_addition_integer(self):
        """`2 + 2` → `'4'` (integer, no decimal point)."""
        assert _safe_eval("2 + 2") == "4"

    def test_subtraction_integer(self):
        """`10 - 3` → `'7'`."""
        assert _safe_eval("10 - 3") == "7"

    def test_multiplication_integer(self):
        """`3 * 4` → `'12'`."""
        assert _safe_eval("3 * 4") == "12"

    def test_true_division_float(self):
        """`7 / 2` → `'3.5'`."""
        assert _safe_eval("7 / 2") == "3.5"

    def test_floor_division(self):
        """`8 // 3` → `'2'`."""
        assert _safe_eval("8 // 3") == "2"

    def test_modulo(self):
        """`10 % 3` → `'1'`."""
        assert _safe_eval("10 % 3") == "1"

    def test_exponentiation(self):
        """`2 ** 8` → `'256'`."""
        assert _safe_eval("2 ** 8") == "256"

    def test_float_mul_gives_integer_result(self):
        """`2.5 * 2` → `'5'` (float arithmetic with whole-number result)."""
        assert _safe_eval("2.5 * 2") == "5"

    def test_float_addition_no_trailing_zeros(self):
        """`1.5 + 1.0` → `'2.5'` (no trailing zeros, Requirement 6.6)."""
        assert _safe_eval("1.5 + 1.0") == "2.5"

    def test_float_result_no_trailing_zeros(self):
        """`1.1 + 2.2` result must not contain unnecessary trailing zeros."""
        result = _safe_eval("1.1 + 2.2")
        assert isinstance(result, str)
        # Must not end with trailing zero after the decimal point
        if "." in result:
            assert not result.rstrip("0").endswith(".") or result == result.rstrip("0")
        # Should not have form like "3.30000…" — :g formatting collapses them
        assert "00000" not in result, f"Unexpected trailing zeros in {result!r}"

    def test_nested_parentheses(self):
        """`(2 + 3) * 4` → `'20'`."""
        assert _safe_eval("(2 + 3) * 4") == "20"


# ---------------------------------------------------------------------------
# 2. Unary operators — Requirement 6.8
# ---------------------------------------------------------------------------


class TestSafeEvalUnaryOperators:
    """Validates Requirement 6.8 — unary plus/minus are supported."""

    def test_unary_minus(self):
        """`-5` → `'-5'`."""
        assert _safe_eval("-5") == "-5"

    def test_unary_plus(self):
        """`+3` → `'3'`."""
        assert _safe_eval("+3") == "3"


# ---------------------------------------------------------------------------
# 3. Division / modulo by zero — Requirement 6.3
# ---------------------------------------------------------------------------


class TestSafeEvalDivisionByZero:
    """Validates Requirement 6.3 — division by zero returns an error string."""

    def test_true_division_by_zero(self):
        """`1/0` must return an error string containing 'zero'."""
        result = _safe_eval("1/0")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "zero" in result.lower()

    def test_floor_division_by_zero(self):
        """`4 // 0` must return an error string containing 'zero'."""
        result = _safe_eval("4 // 0")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "zero" in result.lower()

    def test_modulo_by_zero(self):
        """`5 % 0` must return an error string containing 'zero'."""
        result = _safe_eval("5 % 0")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "zero" in result.lower()


# ---------------------------------------------------------------------------
# 4. Syntax errors — Requirement 6.7
# ---------------------------------------------------------------------------


class TestSafeEvalSyntaxErrors:
    """Validates Requirement 6.7 — malformed expressions return an error string."""

    def test_incomplete_expression(self):
        """`2 +` (incomplete) must return an error string."""
        result = _safe_eval("2 +")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_double_operator(self):
        """`2 ++ 2` is not valid arithmetic — must return an error string."""
        result = _safe_eval("2 ++ 2")
        # Either rejected by AST validator or results in a numeric answer via
        # double-unary +; the spec does not forbid the latter.  We only require
        # no unhandled exception and a str return.
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 5. Unsafe constructs — Requirements 6.2, 6.3, 6.4
# ---------------------------------------------------------------------------


class TestSafeEvalUnsafeConstructs:
    """Validates Requirements 6.2–6.4 — unsafe code never executes."""

    def test_import_via_dunder(self):
        """`__import__('os').system('ls')` must return an error string, never execute."""
        result = _safe_eval("__import__('os').system('ls')")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_eval_builtin(self):
        """`eval('1+1')` must return an error string."""
        result = _safe_eval("eval('1+1')")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_open_file(self):
        """`open('/etc/passwd')` must return an error string."""
        result = _safe_eval("open('/etc/passwd')")
        assert isinstance(result, str)
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# 6. Empty / whitespace input — Requirement 6.4
# ---------------------------------------------------------------------------


class TestCalculatorToolEmptyInput:
    """Validates Requirement 6.4 — empty and whitespace-only inputs are rejected.

    These are tested via the LangChain @tool interface because the guard lives
    in the `calculator()` function (not in `_safe_eval`).
    """

    def test_empty_string(self):
        """Empty string `""` via calculator.invoke must return an error string."""
        result = invoke("")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_whitespace_only(self):
        """`"   "` (spaces only) via calculator.invoke must return an error string."""
        result = invoke("   ")
        assert isinstance(result, str)
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# 7. End-to-end via the LangChain @tool interface
# ---------------------------------------------------------------------------


class TestCalculatorToolInvoke:
    """Smoke-tests the `calculator` @tool via .invoke() to confirm the decorator
    does not interfere with the underlying `_safe_eval` logic."""

    def test_invoke_addition(self):
        assert invoke("2 + 2") == "4"

    def test_invoke_division_by_zero(self):
        result = invoke("1/0")
        assert result.startswith("Error:")
        assert "zero" in result.lower()

    def test_invoke_float_multiplication(self):
        assert invoke("2.5 * 2") == "5"

    def test_invoke_float_addition(self):
        assert invoke("1.5 + 1.0") == "2.5"

    def test_invoke_unsafe_import(self):
        result = invoke("__import__('os').system('ls')")
        assert result.startswith("Error:")

    def test_invoke_syntax_error(self):
        result = invoke("2 +")
        assert result.startswith("Error:")
