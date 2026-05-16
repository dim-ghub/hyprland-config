"""Rule emitters — ``windowrule`` / ``windowrulev2`` / ``layerrule`` / ``workspace``.

Both line-style (``windowrule = float on, match:class …``) and block-style
(``windowrule { match:class = …; float = on; }``) syntaxes are supported.
Line-style assembly happens here; block-style buffer collection lives in
the document walker because it spans multiple input lines.
"""

from typing import Any

from hyprland_config._core._split import split_top_level
from hyprland_config._lua._emit._format import coerce_value, format_table, split_csv


def coerce_rule_value(value: str) -> Any:
    """Like ``coerce_value`` but also maps ``on``/``off`` to bool.

    The line-style emitter handles this in ``_parse_rule_action`` when the
    action and its value live in the same token; block syntax separates
    them across two lines so we need the same translation here.
    """
    stripped = value.strip()
    if stripped.lower() == "on":
        return True
    if stripped.lower() == "off":
        return False
    return coerce_value(value)


def add_block_rule_field(buffer: dict[str, Any], key: str, value: str) -> None:
    """Add one field from a ``windowrule { … }``-style block to its buffer.

    ``match:PROP = VALUE`` lines build up a nested ``match = {…}`` table;
    everything else lives at the top of the rule. Values pass through the
    same coercion as line-style rules so ``float = on`` → ``true`` etc.
    """
    if key.startswith("match:"):
        prop = key[len("match:") :]
        match = buffer.setdefault("match", {})
        if isinstance(match, dict):
            match[prop] = coerce_rule_value(value)
        return
    buffer[key] = coerce_rule_value(value)


def _parse_rule_action(action: str) -> tuple[str, Any]:
    """Split a rule action like ``opacity 0.9`` into ``(name, value)``.

    Hyprland's modern (v3) windowrule syntax uses ``ACTION VALUE`` for both
    bool flags (``float on`` / ``pin off``) and valued actions (``opacity 0.9``,
    ``bordercolor rgba(…)``, ``suppress_event maximize``). Legacy v1 just has
    a bare flag (``float``).
    """
    head, sep, tail = action.partition(" ")
    head = head.strip()
    tail = tail.strip()
    if not sep:
        return head, True
    low = tail.lower()
    if low == "on":
        return head, True
    if low == "off":
        return head, False
    return head, coerce_value(tail)


def _parse_matchers(parts: list[str], v2: bool) -> dict[str, Any]:
    """Build a ``match = { … }`` table from windowrule matcher tokens.

    Supports all three windowrule syntaxes that may appear in user configs:

    - Modern v3 (Hyprland 0.53+, keyword ``windowrule``):
      ``match:class ^kitty$``, ``match:title bar`` — explicit ``match:`` prefix,
      key/value split on first space.
    - Legacy v2 (keyword ``windowrulev2``): ``class:^kitty$``, ``title:bar`` —
      key:value tokens without the ``match:`` prefix.
    - Legacy v1 (keyword ``windowrule`` without ``match:`` tokens):
      single bare token treated as a class regex.

    Selection is by presence: if any token starts with ``match:``, the whole
    list is parsed in v3 mode, otherwise we fall back to v2 (when the caller
    flagged it) or v1.
    """
    match: dict[str, Any] = {}
    if not parts:
        return match

    if any(p.startswith("match:") for p in parts):
        for token in parts:
            if not token.startswith("match:"):
                continue
            rest = token[len("match:") :]
            key, _, value = rest.partition(" ")
            match[key.strip()] = coerce_value(value.strip())
        return match

    # Legacy v1/v2 share the ``KEY:VALUE`` matcher syntax — try that first.
    for token in parts:
        key, sep, value = token.partition(":")
        if sep:
            match[key.strip()] = coerce_value(value.strip())
    if match:
        return match

    # Truly legacy v1 (``windowrule = float, ^firefox$``) — a single bare
    # regex matches the window class.
    if not v2:
        match["class"] = parts[0]
    return match


def _split_action_and_matchers(parts: list[str]) -> tuple[str, list[str]] | None:
    """Find the action token and return it alongside the remaining matchers.

    Both orders show up in real configs — effect-first (``stay_focused on,
    match:title …``) and match-first (``match:title …, stay_focused on``).
    We figure it out by the ``match:`` prefix: anything with it is a matcher,
    the lone token without it is the action. Without any ``match:`` prefix
    we fall back to legacy v1/v2 (first token is the action).
    """
    if not parts:
        return None

    if any(p.startswith("match:") for p in parts):
        action_tokens = [p for p in parts if not p.startswith("match:")]
        matcher_tokens = [p for p in parts if p.startswith("match:")]
        if not action_tokens:
            return None
        return action_tokens[0], matcher_tokens

    return parts[0], parts[1:]


def emit_windowrule(args: str, *, v2: bool) -> str:
    """Shared implementation for ``windowrule`` and ``windowrulev2``."""
    # Bracket-aware split: regex matchers like ``class:^(foo|bar,baz)$`` carry
    # commas inside parens that a naive ``str.split(",")`` would mangle.
    parts = split_top_level(args)
    split = _split_action_and_matchers(parts)
    if split is None:
        return f"-- malformed windowrule: {args}"
    action_str, matcher_tokens = split
    action_name, action_value = _parse_rule_action(action_str)
    matchers = _parse_matchers(matcher_tokens, v2=v2)
    table: dict[str, Any] = {}
    if matchers:
        table["match"] = matchers
    table[action_name] = action_value
    return f"hl.window_rule({format_table(table, indent=0)})"


def emit_layerrule(args: str) -> str:
    """``layerrule = match:namespace REGEX, EFFECT VALUE`` → ``hl.layer_rule({...})``.

    Accepts both the modern ``match:namespace …, effect …`` form and the
    legacy ``effect, REGEX`` shape. The legacy form treats the second
    token as the namespace regex when no ``match:`` prefix is present
    anywhere.
    """
    # Bracket-aware split — see emit_windowrule for the regex-matcher case.
    parts = split_top_level(args)
    split = _split_action_and_matchers(parts)
    if split is None:
        return f"-- malformed layerrule: {args}"
    action_str, matcher_tokens = split
    action_name, action_value = _parse_rule_action(action_str)

    if matcher_tokens and any(t.startswith("match:") for t in matcher_tokens):
        matchers = _parse_matchers(matcher_tokens, v2=False)
    elif matcher_tokens:
        # Legacy: a single bare regex matches the layer namespace.
        matchers = {"namespace": matcher_tokens[0]}
    else:
        matchers = {}

    table: dict[str, Any] = {}
    if matchers:
        table["match"] = matchers
    table[action_name] = action_value
    return f"hl.layer_rule({format_table(table, indent=0)})"


def emit_workspace_rule(args: str) -> str:
    """``workspace = ID, monitor:DP-1, default:true, ...`` → ``hl.workspace_rule({...})``.

    The first token identifies the workspace selector; the rest are
    ``key:value`` rule fields (monitor, default, persistent, gaps_in, etc.).
    """
    parts = split_csv(args)
    if not parts:
        return f"-- malformed workspace: {args}"
    table: dict[str, Any] = {"workspace": coerce_value(parts[0])}
    for token in parts[1:]:
        key, sep, value = token.partition(":")
        if not sep:
            continue
        table[key.strip()] = coerce_value(value.strip())
    return f"hl.workspace_rule({format_table(table, indent=0)})"
