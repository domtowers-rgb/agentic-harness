import ast
import math
import operator

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10,
}
_NAMES = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numeric constants are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("that function is not allowed")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    if isinstance(node, ast.Name):
        if node.id in _NAMES:
            return _NAMES[node.id]
        raise ValueError(f"unknown name: {node.id}")
    raise ValueError(f"expression not allowed: {type(node).__name__}")


def calculate(expression: str):
    """Safely evaluate an arithmetic expression without using eval()."""
    try:
        result = _eval(ast.parse(expression, mode="eval").body)
    except Exception as exc:
        return {"error": f"could not evaluate '{expression}': {exc}"}
    return {"result": result}


def register(registry):
    registry.register("calculate", calculate, {
        "name": "calculate",
        "description": (
            "Evaluate an arithmetic expression. Supports + - * / // % **, "
            "parentheses, and functions like sqrt, abs, round, floor, ceil, "
            "sin, cos, tan, log, log10, plus the constants pi and e."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "e.g. '2 * (3 + 4)' or 'sqrt(16)'"},
            },
            "required": ["expression"],
        },
    })
