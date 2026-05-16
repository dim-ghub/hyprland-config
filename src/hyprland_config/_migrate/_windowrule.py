"""Hyprland 0.48/0.53 windowrule v1↔v2↔v3 transforms.

This module is split out from :mod:`._runner` because the v2→v3
migration alone is several hundred lines of regex- and string-shape
gymnastics. Public surface is the two transform functions that
:mod:`._runner` registers in its ``_MIGRATIONS`` list.
"""

from hyprland_config._core._model import Document, KeyValueLine
from hyprland_config._core._rules import V3_BOOL_EFFECTS
from hyprland_config._migrate._runner import _transform_lines

_V2_PREFIXES = ("title:", "class:", "xwayland:", "floating:", "fullscreen:")


# ---------------------------------------------------------------------------
# v2 → v3 windowrule migration (Hyprland 0.52 → 0.53)
# ---------------------------------------------------------------------------
#
# Hyprland 0.53 introduced a new single-line windowrule syntax::
#
#     windowrule = match:class ^(firefox)$, float on
#
# replacing the v2 form::
#
#     windowrulev2 = float, class:^(firefox)$
#
# Key differences this migration handles:
#
# - Keyword renames from ``windowrulev2`` back to ``windowrule``.
# - Matcher tokens move from ``key:value`` to ``match:KEY VALUE`` —
#   note the space, not a colon, between key and value.
# - Boolean effects (``float``, ``pin``, ``no_blur``, …) require an
#   explicit ``on`` argument; v2 allowed them bare.
# - Several effects and matchers were renamed (``noblur`` → ``no_blur``,
#   ``initialClass`` → ``initial_class``, etc.).
# - Negation moved from a leading ``~`` on the matcher to a
#   ``negative:`` prefix on the value.

# v2 → v3 effect renames. Anything not in this dict is assumed to be
# the same name in v3 (including custom plugin actions).
_V2_TO_V3_EFFECT: dict[str, str] = {
    "noblur": "no_blur",
    "noshadow": "no_shadow",
    "noborder": "no_border",
    "noanim": "no_anim",
    "nodim": "no_dim",
    "nofocus": "no_focus",
    "noinitialfocus": "no_initial_focus",
    "nofollowmouse": "no_follow_mouse",
    "noshortcutsinhibit": "no_shortcuts_inhibit",
    "noscreenshare": "no_screen_share",
    "novrr": "no_vrr",
    "norounding": "no_rounding",
    "nomaxsize": "no_max_size",
    "stayfocused": "stay_focused",
    "idleinhibit": "idle_inhibit",
    "bordercolor": "border_color",
    "bordersize": "border_size",
    "maxsize": "max_size",
    "minsize": "min_size",
    "suppressevent": "suppress_event",
    "noclosefor": "no_close_for",
    "syncfullscreen": "sync_fullscreen",
    "forcergbx": "force_rgbx",
    "focusonactivate": "focus_on_activate",
    "keepaspectratio": "keep_aspect_ratio",
    "nearestneighbor": "nearest_neighbor",
    "renderunfocused": "render_unfocused",
    "scrollmouse": "scroll_mouse",
    "scrolltouchpad": "scroll_touchpad",
    "scrollingwidth": "scrolling_width",
    "allowsinput": "allows_input",
    "dimaround": "dim_around",
    "persistentsize": "persistent_size",
    "fullscreenstate": "fullscreen_state",
    "roundingpower": "rounding_power",
}


# v2 → v3 matcher key renames. Note ``floating`` → ``float`` and
# ``pinned`` → ``pin``: in v3 the matcher key is the same word as the
# corresponding effect, which matches the wiki.
_V2_TO_V3_MATCHER: dict[str, str] = {
    "initialClass": "initial_class",
    "initialTitle": "initial_title",
    "floating": "float",
    "pinned": "pin",
    "xdgtag": "xdg_tag",
    # ``onworkspace`` collapsed into ``workspace`` in v3.
    "onworkspace": "workspace",
    "fullscreenstate": "fullscreen_state",
}


# Full set of known v3 effect names. Used by the corruption-recovery
# heuristic in :func:`_uncorrupt_v3_pretending_to_be_v2` to decide
# whether a token after ``title:`` is a real v3 effect (signalling
# corrupted output from buggy older migrations) vs. a legitimate
# v2 ``title:<regex>`` matcher.
_V3_EFFECT_NAMES: frozenset[str] = V3_BOOL_EFFECTS | frozenset(
    {
        "opacity", "size", "move", "workspace", "monitor", "rounding",
        "rounding_power", "border_color", "border_size", "min_size",
        "max_size", "idle_inhibit", "animation", "scroll_mouse",
        "scroll_touchpad", "suppress_event", "tag", "xdg_tag",
        "no_close_for", "fullscreen_state", "scrolling_width",
    }
)  # fmt: skip


def _split_v2_matchers(raw: str) -> list[str]:
    """Split a v2 matcher string into per-matcher tokens.

    v2 accepted commas and whitespace as separators.
    """
    tokens: list[str] = []
    for chunk in raw.split(","):
        for tok in chunk.split():
            if tok:
                tokens.append(tok)
    return tokens


def _v2_body_to_v3(body: str) -> str:
    """Translate a v2 windowrule body string to its v3 equivalent.

    Input: ``ACTION, MATCHERS`` where MATCHERS are ``key:value``
    tokens space- or comma-separated.

    Output: ``match:KEY VALUE, …, EFFECT [args]`` with the effect
    coming last (the v3 conventional order).

    Pure string-to-string transformation — no Document mutation, no
    side effects.
    """
    action_part, _, matcher_part = body.partition(",")
    action = action_part.strip()
    if not action:
        return body  # malformed; leave alone

    # Split the action into name + args (e.g. "size 1920 1080").
    action_name, _, action_args = action.partition(" ")
    new_name = _V2_TO_V3_EFFECT.get(action_name, action_name)
    args = action_args.strip()
    # Boolean effects in v2 had no args; in v3 they require ``on``.
    if not args and new_name in V3_BOOL_EFFECTS:
        args = "on"
    effect = f"{new_name} {args}".strip() if args else new_name

    matcher_tokens: list[str] = []
    for tok in _split_v2_matchers(matcher_part):
        # v2 negation was ``~key:value`` — strip the ``~`` and prepend
        # ``negative:`` to the value, the v3 form.
        negated = tok.startswith("~")
        if negated:
            tok = tok[1:]
        key, sep, value = tok.partition(":")
        if not sep:
            # Unparseable token — preserve verbatim so nothing is lost.
            matcher_tokens.append(("~" if negated else "") + tok)
            continue
        new_key = _V2_TO_V3_MATCHER.get(key.strip(), key.strip())
        new_value = value.strip()
        if negated and new_value:
            new_value = f"negative:{new_value}"
        matcher_tokens.append(f"match:{new_key} {new_value}")

    return ", ".join([*matcher_tokens, effect])


def _uncorrupt_v3_pretending_to_be_v2(body: str) -> str | None:
    """Try to recover a v3 body from the ``hyprland-config<0.4.4`` corruption.

    Versions of this library before 0.4.4 had a v1→v2 windowrule
    migration that didn't recognise v3 syntax and incorrectly fired
    on v3 lines, producing output of the shape::

        windowrulev2 = <v3 matcher>, title:<v3 effect> <effect args>

    where the ``title:`` prefix and the keyword rename are bogus
    (the input was already valid v3). When we see a ``windowrulev2``
    line that contains a ``match:`` token (a v3 marker) AND a
    ``title:<known-effect>`` token, we strip the bogus ``title:``
    and return the cleaned body so the v3 parser can take over.

    Returns ``None`` if the line doesn't match the corruption pattern,
    in which case the caller should treat it as a regular v2 line.
    """
    # v3 marker: at least one comma-separated token starts with ``match:``.
    tokens = [t.strip() for t in body.split(",")]
    if not any(t.startswith("match:") for t in tokens):
        return None

    repaired: list[str] = []
    fixed_one = False
    for tok in tokens:
        if not fixed_one and tok.startswith("title:"):
            inner = tok[len("title:") :].strip()
            head, _, _ = inner.partition(" ")
            if head and head in _V3_EFFECT_NAMES:
                # Strip the bogus title:; the rest is the original
                # effect token.
                repaired.append(inner)
                fixed_one = True
                continue
        repaired.append(tok)

    if not fixed_one:
        return None
    return ", ".join(repaired)


def migrate_windowrule_v2_to_v3(doc: Document) -> bool:
    """Convert ``windowrulev2`` (v2) lines to ``windowrule`` (v3) syntax.

    Handles two cases that share the keyword:

    1. **Real v2 lines** (``windowrulev2 = float, class:^(firefox)$``)
       are translated to v3 (``windowrule = match:class ^(firefox)$,
       float on``) via :func:`_v2_body_to_v3`.
    2. **Corrupted-v3 lines** — output of a buggy older v1→v2
       migration that wrapped v3 syntax in v2 packaging — are
       recovered to their original v3 form via
       :func:`_uncorrupt_v3_pretending_to_be_v2`. The check fires
       only when the body contains a ``match:`` token (a v3 marker
       that real v2 never uses) plus a stray ``title:<v3 effect>``.
    """

    def predicate(line: KeyValueLine) -> bool:
        return line.key == "windowrulev2"

    def transform(line: KeyValueLine) -> None:
        recovered = _uncorrupt_v3_pretending_to_be_v2(line.value)
        new_value = recovered if recovered is not None else _v2_body_to_v3(line.value)
        line.key = "windowrule"
        line.full_key = line.full_key.replace("windowrulev2", "windowrule", 1)
        line.value = new_value
        line.update_raw()

    return _transform_lines(doc, predicate, transform)


def migrate_windowrule_v1_to_v2(doc: Document) -> bool:
    """Convert windowrule (v1) to windowrulev2 syntax.

    Only triggers on lines that look unambiguously like v1 — the
    "rule, regex" two-part form with no v2/v3 prefixes anywhere. v3
    Hyprland 0.53+ reused the ``windowrule`` keyword for a different
    syntax (``windowrule = match:KEY VALUE, EFFECT VALUE``); we MUST
    NOT migrate v3 lines back to v2 form, because Hyprland 0.53+
    rejects v2 outright. v3 lines always carry at least one
    ``match:`` token, so the presence of ``match:`` anywhere in the
    value is a hard "this is not v1" signal.
    """

    def predicate(line: KeyValueLine) -> bool:
        if line.key != "windowrule":
            return False
        # v3 lines are recognised by the ``match:`` prefix on at
        # least one of their comma-separated tokens. Skip those —
        # migrating them as v1 would corrupt the line and produce
        # invalid v2 syntax that Hyprland 0.53+ rejects.
        if any(t.strip().startswith("match:") for t in line.value.split(",")):
            return False
        parts = line.value.split(",", 1)
        if len(parts) != 2:
            return False
        window = parts[1].strip()
        return not any(window.startswith(p) for p in _V2_PREFIXES)

    def transform(line: KeyValueLine) -> None:
        rule, window = (p.strip() for p in line.value.split(",", 1))
        line.key = "windowrulev2"
        line.full_key = line.full_key.replace("windowrule", "windowrulev2", 1)
        line.value = f"{rule}, title:{window}"
        line.update_raw()

    return _transform_lines(doc, predicate, transform)
