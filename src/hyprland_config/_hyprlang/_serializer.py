"""Document → Hyprlang text serializer.

The :class:`Document` AST stores each line's already-rendered Hyprlang
text on its ``raw`` field — both the parser and the Lua reader populate
``raw`` with Hyprlang-formatted text so consumers see one canonical
shape. Serialization is mostly a trivial join of those raw strings;
:class:`Rule` is the one structured node that renders on demand via
:func:`render_rule_hyprlang` (block form vs. single-line is picked from
the rule's fields, not stored as text up front).

Rules also render in a *version*-specific grammar. Hyprland adopted the
v3 ``match:`` form for windowrules in 0.53 and for layerrules in 0.54;
older compositors only understand the effect-first form
(``windowrulev2 = float, class:^(x)$`` / ``layerrule = blur, ^(x)$``).
:func:`render_rule_hyprlang` and :func:`render_rule_live` take the
running version and emit the grammar that compositor can parse, falling
back to v3 when the version is unknown.
"""

from hyprland_config._core._model import Document, Rule
from hyprland_config._core._rules import (
    LAYER_BOOL_EFFECTS,
    LAYERRULE_V3_VERSION,
    V3_BOOL_EFFECTS,
    V3_TO_LEGACY_LAYER_EFFECT,
    V3_TO_V2_EFFECT,
    V3_TO_V2_MATCHER,
    WINDOWRULE_V3_VERSION,
)
from hyprland_config._core._types import parse_version


def serialize_hyprlang(doc: Document, version: str | None = None) -> str:
    """Reconstruct *doc*'s Hyprlang source text from its line nodes.

    *version* is the running Hyprland version (e.g. ``"0.49.0"``); rule
    nodes render in the grammar that version understands. ``None`` — the
    default — always emits the v3 form.
    """
    return "".join(
        render_rule_hyprlang(line, version) if isinstance(line, Rule) else line.raw
        for line in doc.lines
    )


def render_rule_hyprlang(rule: Rule, version: str | None = None) -> str:
    """Render *rule* as the Hyprlang on-disk form for *version*.

    At or above the rule kind's v3 boundary (and when *version* is
    ``None``), emits v3: the block form (``windowrule { name = …; … }``)
    when the rule is named or disabled — those fields only exist in block
    syntax — otherwise the compact single line, which Hyprland accepts
    even for multi-effect rules. Below the boundary, emits the pre-v3
    effect-first form, one line per effect (the old grammar has neither
    multi-effect lines nor named/disabled rules).
    """
    if _predates_v3(rule, version):
        return _render_pre_v3(rule)
    if rule.name or not rule.enabled:
        return _render_block(rule)
    return _render_single_line(rule)


def render_rule_live(rule: Rule, version: str | None = None) -> list[tuple[str, str]]:
    """``(keyword, value)`` pairs to push via ``hyprctl keyword`` for live-apply.

    The keyword name tracks the grammar (``windowrule`` vs. the pre-v3
    ``windowrulev2``). ``name`` and ``enabled`` are dropped — Hyprland's
    single-line keyword handler rejects them, and callers gate disabled
    rules before applying. Pre-v3 multi-effect rules yield one pair per
    effect (the old grammar is one effect per line); v3 yields a single
    pair carrying every effect.
    """
    if _predates_v3(rule, version):
        return _pre_v3_commands(rule)
    return [(rule.kind, _v3_single_line_body(rule))]


def _predates_v3(rule: Rule, version: str | None) -> bool:
    """True when *version* is older than *rule*'s kind gained v3 syntax."""
    if version is None:
        return False
    boundary = LAYERRULE_V3_VERSION if rule.kind == "layerrule" else WINDOWRULE_V3_VERSION
    return parse_version(version) < boundary


def _bool_effects(kind: str) -> frozenset[str]:
    """The bare-bool effect set for *kind* (layer and window differ)."""
    return LAYER_BOOL_EFFECTS if kind == "layerrule" else V3_BOOL_EFFECTS


# ---------------------------------------------------------------------------
# v3 rendering
# ---------------------------------------------------------------------------


def _v3_single_line_body(rule: Rule) -> str:
    """``match:K V, EFFECT [ARGS]`` — the value half of a v3 single-line rule."""
    bool_effects = _bool_effects(rule.kind)
    parts: list[str] = [f"match:{k} {v}" for k, v in rule.matchers]
    parts.extend(_v3_effect_inline(name, args, bool_effects) for name, args in rule.effects)
    return ", ".join(parts)


def _render_single_line(rule: Rule) -> str:
    return f"{rule.kind} = {_v3_single_line_body(rule)}\n"


def _render_block(rule: Rule) -> str:
    """``windowrule { name = X; match:K = V; EFFECT = args; }`` block form."""
    bool_effects = _bool_effects(rule.kind)
    lines: list[str] = [f"{rule.kind} {{"]
    if rule.name:
        lines.append(f"    name = {rule.name}")
    if not rule.enabled:
        lines.append("    enable = 0")
    for k, v in rule.matchers:
        lines.append(f"    match:{k} = {v}")
    for name, args in rule.effects:
        rendered = _v3_effect_value(name, args, bool_effects)
        lines.append(f"    {name} = {rendered}" if rendered else f"    {name} =")
    lines.append("}\n")
    return "\n".join(lines)


def _v3_effect_inline(name: str, args: str, bool_effects: frozenset[str]) -> str:
    """``NAME [ARGS]`` for single-line form (bool effects default to ``on``)."""
    args = args.strip()
    if not args and name in bool_effects:
        args = "on"
    return f"{name} {args}" if args else name


def _v3_effect_value(name: str, args: str, bool_effects: frozenset[str]) -> str:
    """Value half of ``NAME = VALUE`` in block form (bool defaults ``on``)."""
    args = args.strip()
    if not args and name in bool_effects:
        args = "on"
    return args


# ---------------------------------------------------------------------------
# Pre-v3 rendering (effect-first grammar)
# ---------------------------------------------------------------------------


def _render_pre_v3(rule: Rule) -> str:
    """On-disk text in the pre-v3 effect-first grammar.

    Named and disabled rules have no pre-v3 equivalent (both arrived with
    the v3 grammar), so the name is recorded in a comment and a disabled
    rule's lines are commented out — keeping the file valid for the older
    compositor without silently discarding the user's intent.
    """
    lines = [f"{keyword} = {value}" for keyword, value in _pre_v3_commands(rule)]
    notes: list[str] = []
    if rule.name:
        notes.append(f"# name '{rule.name}' dropped: named rules require Hyprland v3")
    if not rule.enabled:
        notes.append("# disabled rule: no enable flag before Hyprland v3")
        lines = [f"# {line}" for line in lines]
    return "\n".join([*notes, *lines]) + "\n"


def _pre_v3_commands(rule: Rule) -> list[tuple[str, str]]:
    """``(keyword, value)`` pairs in the pre-v3 effect-first grammar."""
    if rule.kind == "layerrule":
        keyword = "layerrule"
        # Layer rules match one namespace regex, placed bare after the
        # effect — no ``match:`` / ``namespace:`` prefix before 0.54.
        trailing = [v for k, v in rule.matchers if k == "namespace"]
    else:
        keyword = "windowrulev2"
        trailing = [_pre_v3_window_matcher(k, v) for k, v in rule.matchers]
    return [
        (keyword, ", ".join([_pre_v3_effect(rule.kind, name, args), *trailing]))
        for name, args in rule.effects
    ]


def _pre_v3_window_matcher(key: str, value: str) -> str:
    """``class:^(x)$`` (or negated ``~class:^(x)$``) from a v3 matcher pair."""
    negated = value.startswith("negative:")
    if negated:
        value = value[len("negative:") :]
    token = f"{V3_TO_V2_MATCHER.get(key, key)}:{value}"
    return f"~{token}" if negated else token


def _pre_v3_effect(kind: str, name: str, args: str) -> str:
    """Effect token in the pre-v3 grammar (old name, no bare-bool ``on``)."""
    args = args.strip()
    old_name = (
        V3_TO_LEGACY_LAYER_EFFECT.get(name, name)
        if kind == "layerrule"
        else V3_TO_V2_EFFECT.get(name, name)
    )
    # Pre-v3 bool effects are bare; drop the v3-mandated ``on``.
    if name in _bool_effects(kind) and (not args or args.lower() == "on"):
        return old_name
    return f"{old_name} {args}" if args else old_name
