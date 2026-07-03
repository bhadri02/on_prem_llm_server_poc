"""Temporary smoke test for calculator implementation."""
from tools.calculator import _safe_eval, calculator_tool

tests = [
    ("2 + 2", "4"),
    ("1/0", "Error: Division by zero"),
    ("2.5 * 2", "5"),
    ("1.5 + 1.0", "2.5"),
    ("10 // 3", "3"),
    ("10 % 3", "1"),
    ("2 ** 10", "1024"),
    ("-5", "-5"),
    ("+3", "3"),
    ("3.14", "3.14"),
    ("", "Error: expression must be a non-empty string"),
    ("   ", "Error: expression must be a non-empty string"),
]

all_pass = True
for expr, expected in tests:
    if expr == "" or expr.strip() == "":
        result = calculator_tool.invoke({"expression": expr})
    else:
        result = _safe_eval(expr)
    status = "PASS" if result == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"[{status}] _safe_eval({expr!r}) = {result!r}  (expected {expected!r})")

# Security: attribute access, function call should be blocked
unsafe_cases = [
    "open('/etc/passwd')",
    "1 + (1).__class__",
    "[1,2,3][0]",
]
for expr in unsafe_cases:
    result = _safe_eval(expr)
    blocked = result.startswith("Error:")
    status = "PASS" if blocked else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"[{status}] unsafe {expr!r} -> {result!r}")

print()
print("ALL TESTS PASSED" if all_pass else "SOME TESTS FAILED")
