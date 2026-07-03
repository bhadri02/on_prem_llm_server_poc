import ast
from langchain_core.tools import tool

# Permitted AST node types
_SAFE_NODES_LIST = [
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,     # Python 3.8+ numeric literal
    # Permitted operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    # Unary
    ast.UAdd, ast.USub,
]
# ast.Num was removed in Python 3.14; guard for older versions
if hasattr(ast, "Num"):
    _SAFE_NODES_LIST.append(ast.Num)  # type: ignore[attr-defined]
_SAFE_NODES = tuple(_SAFE_NODES_LIST)


class _SafeEvaluator(ast.NodeVisitor):
    """Evaluates only whitelisted arithmetic AST nodes. Raises ValueError on any other node."""

    def visit(self, node: ast.AST):
        if not isinstance(node, _SAFE_NODES):
            raise ValueError(
                f"Unsafe expression: '{type(node).__name__}' is not permitted"
            )
        return super().visit(node)

    def visit_Expression(self, node): return self.visit(node.body)

    def visit_Constant(self, node):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Non-numeric literal: {node.value!r}")
        return node.value

    def visit_Num(self, node): return node.n  # Python ≤ 3.7 compatibility

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = node.op
        if isinstance(op, ast.Add):    return left + right
        if isinstance(op, ast.Sub):    return left - right
        if isinstance(op, ast.Mult):   return left * right
        if isinstance(op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left / right
        if isinstance(op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left // right
        if isinstance(op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError("Modulo by zero")
            return left % right
        if isinstance(op, ast.Pow):    return left ** right
        raise ValueError(f"Unsupported operator: {type(op).__name__}")

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.USub): return -operand
        if isinstance(node.op, ast.UAdd): return +operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")


def _safe_eval(expression: str) -> str:
    """Parse and evaluate an arithmetic expression string. Returns result as string."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        return f"Error: invalid expression syntax: {expression!r}"

    evaluator = _SafeEvaluator()
    try:
        result = evaluator.visit(tree)
    except ValueError as exc:
        return f"Error: {exc}"
    except ZeroDivisionError as exc:
        return f"Error: {exc}"
    except OverflowError:
        return "Error: numeric overflow"

    # Format: integer result → no decimal point; float → strip trailing zeros
    if isinstance(result, float) and result.is_integer():
        return str(int(result))
    if isinstance(result, float):
        return f"{result:g}"
    return str(result)


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression. Returns the result as a string."""
    if not expression or not expression.strip():
        return "Error: expression must be a non-empty string"
    return _safe_eval(expression)
