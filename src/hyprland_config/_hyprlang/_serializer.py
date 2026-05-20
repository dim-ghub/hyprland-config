"""Document → Hyprlang text serializer.

The :class:`Document` AST stores each line's already-rendered Hyprlang
text on its ``raw`` field — both the parser and the Lua reader populate
``raw`` with Hyprlang-formatted text so consumers see one canonical
shape. Serialization is mostly a trivial join of those raw strings;
:class:`Rule` is the one structured node that renders on demand via
:func:`render_rule_hyprlang` (block form vs. single-line is picked from
the rule's fields, not stored as text up front).
"""

from hyprland_config._core._model import Document, Rule
from hyprland_config._core._rules import V3_BOOL_EFFECTS


def serialize_hyprlang(doc: Document) -> str:
    """Reconstruct *doc*'s Hyprlang source text from its line nodes."""
    return "".join(
        render_rule_hyprlang(line) if isinstance(line, Rule) else line.raw for line in doc.lines
    )


def render_rule_hyprlang(rule: Rule) -> str:
    """Render *rule* as the Hyprlang on-disk form.

    Block form (``windowrule { name = …; … }``) is used when the rule
    carries a name or is disabled — those fields only exist in block
    syntax (Hyprland's single-line handler rejects them). Anonymous,
    enabled rules — including multi-effect ones — emit as the compact
    one-line form, which Hyprland accepts and matches what users
    typically author.
    """
    needs_block = bool(rule.name) or not rule.enabled
    if needs_block:
        return _render_block(rule)
    return _render_single_line(rule)


def _render_single_line(rule: Rule) -> str:
    """``windowrule = match:K V, EFFECT [ARGS]`` for the compact case."""
    parts: list[str] = [f"match:{k} {v}" for k, v in rule.matchers]
    parts.extend(_render_effect_inline(name, args) for name, args in rule.effects)
    return f"{rule.kind} = {', '.join(parts)}\n"


def _render_block(rule: Rule) -> str:
    """``windowrule { name = X; match:K = V; EFFECT = args; }`` block form."""
    lines: list[str] = [f"{rule.kind} {{"]
    if rule.name:
        lines.append(f"    name = {rule.name}")
    if not rule.enabled:
        lines.append("    enable = 0")
    for k, v in rule.matchers:
        lines.append(f"    match:{k} = {v}")
    for name, args in rule.effects:
        rendered = _render_effect_value(name, args)
        lines.append(f"    {name} = {rendered}" if rendered else f"    {name} =")
    lines.append("}\n")
    return "\n".join(lines)


def _render_effect_inline(name: str, args: str) -> str:
    """``NAME [ARGS]`` for single-line form (bool effects default to ``on``)."""
    args = args.strip()
    if not args and name in V3_BOOL_EFFECTS:
        args = "on"
    return f"{name} {args}" if args else name


def _render_effect_value(name: str, args: str) -> str:
    """Value half of ``NAME = VALUE`` in block form (bool defaults ``on``)."""
    args = args.strip()
    if not args and name in V3_BOOL_EFFECTS:
        args = "on"
    return args
