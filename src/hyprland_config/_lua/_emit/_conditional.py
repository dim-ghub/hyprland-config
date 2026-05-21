"""Translate Hyprlang conditional expressions to Lua.

Hyprlang gates blocks of config with ``# hyprlang if EXPR`` /
``# hyprlang elif EXPR`` directives. The expression syntax is intentionally
narrow: a ``$VAR`` left-hand reference, a comparison operator, and a
literal right-hand side. The translator emits the matching Lua expression
so the generated config preserves the conditional behavior at Lua load time.

Mapping rules:

- ``$VAR == LIT`` / ``$VAR != LIT`` — string compare with the RHS quoted as
  a Lua string. ``!=`` becomes Lua ``~=``.
- ``$VAR > N`` / ``$VAR < N`` / ``>=`` / ``<=`` — numeric compare. Hyprlang
  variables are strings, so the LHS is wrapped in ``tonumber(...)`` and the
  RHS must parse as an integer or float.
- Bare ``$VAR`` (truthy check) — emit a Lua expression that matches
  Hyprland's "non-empty, non-zero, non-false-string" semantics.

Compound boolean expressions (``and`` / ``or`` / ``not``) and anything else
that doesn't fit the shapes above return ``None``. The walker's caller
treats that as a signal to leave the entire conditional in the trailing
manual-conversion block — fail loudly instead of guessing.
"""

import re

from hyprland_config._core._values import FLOAT_LITERAL_RE, INT_LITERAL_RE
from hyprland_config._lua._emit._format import lua_var_name, quote_string

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_BARE_VAR_RE = re.compile(rf"^\$({_IDENT})$")
_BINOP_RE = re.compile(rf"^\$({_IDENT})\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")

_QUOTED_RE = re.compile(r"""^(['"])(.*)\1$""")

_NUMERIC_OPS = frozenset({">", "<", ">=", "<="})

# Token boundary check for boolean connectives — used to reject expressions
# like ``$X == nvidia or $Y == amd``. The equality-operator regex above would
# otherwise swallow ``nvidia or $Y == amd`` as the RHS string literal.
_BOOL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_$])(and|or|not)(?![A-Za-z0-9_])")


def translate_expression(expr: str) -> tuple[str, set[str]] | None:
    """Translate one Hyprlang conditional expression.

    Returns ``(lua_expression, referenced_variable_names)`` on success, or
    ``None`` when the expression doesn't match a supported shape. The caller
    surfaces unmatched expressions in the manual-conversion block.
    """
    expr = expr.strip()
    if not expr:
        return None

    # Reject compound boolean expressions early — the binary-op regex would
    # happily match the LHS and swallow the rest as a quoted RHS, producing
    # silently wrong Lua. Bail to the caller so the original directive lands
    # in the manual-conversion block instead.
    if _BOOL_TOKEN_RE.search(expr):
        return None

    m = _BINOP_RE.match(expr)
    if m:
        var_name, op, rhs_raw = m.group(1), m.group(2), m.group(3).strip()
        local = lua_var_name(var_name)
        if op in _NUMERIC_OPS:
            rhs_num = _parse_number_literal(rhs_raw)
            if rhs_num is None:
                return None
            return (f"tonumber({local}) {op} {rhs_num}", {var_name})
        # The preamble emits variables with their natural type (numbers as
        # numbers, bools as booleans). Hyprlang's ``$x == 1`` comparison is
        # string-typed, so we ``tostring(...)`` the LHS to make the
        # equality match whether the local turned out to be a string,
        # number, or bool.
        lua_op = "~=" if op == "!=" else "=="
        rhs_lua = _quote_rhs(rhs_raw)
        return (f"tostring({local}) {lua_op} {rhs_lua}", {var_name})

    m = _BARE_VAR_RE.match(expr)
    if m:
        name = m.group(1)
        local = lua_var_name(name)
        # Hyprlang truthy: non-nil, non-empty, non-"0", non-"false". The
        # ``~= nil`` check stays unwrapped so an undefined local (Lua nil)
        # short-circuits to falsy — ``tostring(nil)`` is the string ``"nil"``,
        # which would otherwise sneak past the falsy checks. The remaining
        # checks go through ``tostring`` so numeric ``0`` and boolean
        # ``false`` locals (from the typed preamble) coerce to their string
        # forms and trip the same patterns Hyprlang uses.
        truthy = (
            f'({local} ~= nil and tostring({local}) ~= "" '
            f'and tostring({local}) ~= "0" and tostring({local}) ~= "false")'
        )
        return (truthy, {name})

    return None


def _parse_number_literal(text: str) -> str | None:
    """Return *text* if it's an int/float literal Lua can compare against."""
    text = text.strip()
    if INT_LITERAL_RE.match(text) or FLOAT_LITERAL_RE.match(text):
        return text
    return None


def _quote_rhs(rhs: str) -> str:
    """Quote a comparison RHS as a Lua string.

    Already-quoted literals (single or double) are unwrapped first so the
    output isn't double-quoted. Bare words (``nvidia``, ``amd``) are quoted
    as Lua strings — Hyprlang treats them the same way.
    """
    m = _QUOTED_RE.match(rhs)
    if m:
        return quote_string(m.group(2))
    return quote_string(rhs)
