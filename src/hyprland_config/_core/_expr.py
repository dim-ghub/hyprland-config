"""Expression evaluator for Hyprland {{expr}} syntax.

Supports integer and float arithmetic: +, -, *, /, %, parentheses.
Variable references ($var) should be expanded before evaluation.
"""

import ast
import operator
import re
from collections.abc import Callable

# Hyprlang ``$var`` token. Maximal-munch tokenisation (``$mainMod`` is one
# token, not ``$main`` plus literal ``Mod``) matches Hyprland's own parser and
# avoids the prefix-collision class of bugs a substring-replace approach has.
_VAR_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_-]*)")

_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class ExprError(Exception):
    """Raised when an expression cannot be evaluated."""


def _eval_node(node: ast.AST) -> int | float:
    """Recursively evaluate an AST node, allowing only arithmetic on numeric constants."""
    if isinstance(node, ast.Constant):
        # bool is a subclass of int — reject so True/False aren't treated as numbers
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExprError(f"unsupported literal: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExprError(f"unsupported operator: {type(node.op).__name__}")
        try:
            return op(_eval_node(node.left), _eval_node(node.right))
        except ZeroDivisionError:
            name = "division" if isinstance(node.op, ast.Div) else "modulo"
            raise ExprError(f"{name} by zero") from None
    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise ExprError(f"unsupported unary operator: {type(node.op).__name__}")
        return unary(_eval_node(node.operand))
    raise ExprError(f"unsupported expression node: {type(node).__name__}")


def evaluate_expression(expr: str) -> int | float:
    """Evaluate a simple arithmetic expression.

    Returns int when the result is a whole number, float otherwise.
    """
    expr = expr.strip()
    if not expr:
        raise ExprError("empty expression")
    if expr.count("(") != expr.count(")"):
        raise ExprError("mismatched parentheses")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"invalid expression: {e.msg or e}") from None
    result = _eval_node(tree.body)
    # Return int when possible for cleaner output
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def expand_value(text: str, variables: dict[str, str]) -> str:
    """Fully expand a Hyprland config value.

    Performs three transformations in order:
    1. Variable substitution — ``$var`` references are replaced
       longest-name-first to avoid prefix collisions.
    2. Expression evaluation — ``{{expr}}`` blocks are evaluated as
       arithmetic expressions.
    3. Escape processing — backslash escapes (``\\\\``, ``\\{``) are
       resolved per hyprlang 0.6.4+ rules.
    """
    result = text
    # Sort by length (longest first) to avoid prefix collisions when replacing.
    for name in sorted(variables, key=len, reverse=True):
        result = result.replace(f"${name}", variables[name])
    if "{{" in result or "\\" in result:
        result = expand_expressions(result)
    return result


def substitute_variables_with_markers(
    text: str,
    variables: dict[str, str],
    referenced: dict[str, str],
    open_marker: str,
    close_marker: str,
) -> str:
    """Replace ``$var`` references with ``OPEN<name>CLOSE`` placeholder tokens.

    Records each substituted variable in *referenced* (name → value) so the
    caller can emit matching ``local`` declarations elsewhere. Variable
    *values* are scanned recursively in post-order: ``$accent = $primary``
    registers ``primary`` *before* ``accent`` so the caller's preamble
    declares ``local var_primary`` ahead of ``local var_accent`` — Lua
    evaluates each local's RHS at declaration time, so a dependency
    declared later would resolve to ``nil``. Variables whose name isn't
    in *variables* survive as ``$name`` literals — same fall-through
    behaviour as :func:`expand_value` so unknown references don't
    disappear silently.

    Tokenisation is maximal-munch via :data:`_VAR_REF_RE`: ``$mainMod`` is
    one reference even when both ``$mainMod`` and ``$main`` exist, and
    ``$foo123`` doesn't accidentally consume ``$foo`` if only ``$foo`` is
    defined. Matches Hyprland's own parser.

    Used by the Lua emitter to keep variable references symbolic across
    the assignment / bind / keyword pipelines (see
    :func:`hyprland_config._lua._emit._format.to_lua_expr`).
    """
    if "$" not in text:
        return text
    in_progress: set[str] = set()

    def register(name: str) -> None:
        # in_progress breaks cycles ($a → $b → $a) — one of the two ends
        # up registered without its peer available, which is the best
        # Lua can do anyway.
        if name in referenced or name not in variables or name in in_progress:
            return
        in_progress.add(name)
        value = variables[name]
        for m in _VAR_REF_RE.finditer(value):
            if m.group(1) in variables:
                register(m.group(1))
        referenced[name] = value
        in_progress.discard(name)

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        register(name)
        return f"{open_marker}{name}{close_marker}"

    result = _VAR_REF_RE.sub(_replace, text)
    if "{{" in result or "\\" in result:
        result = expand_expressions(result)
    return result


def expand_expressions(text: str) -> str:
    """Replace all ``{{expr}}`` in text with their evaluated results.

    Handles escape sequences (hyprlang 0.6.4+):

    - ``\\{{expr}}`` or ``{\\{expr}}`` or ``\\{\\{expr}}`` prevents
      evaluation; the backslashes are stripped and ``{{expr}}`` is
      kept verbatim.
    - ``\\\\{{expr}}`` produces a literal backslash followed by the
      evaluated expression result.

    Expressions that fail to evaluate are left unchanged.
    """
    if "{{" not in text and "\\" not in text:
        return text

    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "\\":
                # \\\\ → literal backslash
                result.append("\\")
                i += 2
            elif nxt == "{":
                # \\{ → literal {, prevents expression opening
                result.append("{")
                i += 2
            else:
                result.append(ch)
                i += 1
        elif ch == "{" and i + 1 < n and text[i + 1] == "{":
            # {{ — find closing }} and evaluate
            end = text.find("}}", i + 2)
            if end != -1:
                expr_str = text[i + 2 : end]
                try:
                    val = evaluate_expression(expr_str)
                    result.append(str(val))
                except ExprError:
                    result.append(text[i : end + 2])
                i = end + 2
            else:
                result.append(ch)
                i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result)
