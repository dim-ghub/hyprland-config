"""Per-keyword shape converters — Lua call args back to Hyprlang text.

Each converter takes the recorded ``hl.<keyword>`` table and returns
the Hyprlang CSV string the original config would have written. The
result feeds into a :class:`Keyword` line on the synthesised Document
so that consumers see the same line shape regardless of which side
wrote it.
"""

from typing import Any

from hyprland_config._core._model import Assignment, Document, SectionClose, SectionOpen
from hyprland_config._lua._read._config import format_number, scalar_to_hyprlang
from hyprland_config._lua._workspace_fields import lua_field_to_hyprlang


def monitor_value(t: dict[str, Any]) -> str:
    """Reassemble ``hl.monitor({...})`` into ``output, mode, position, scale[, k, v...]``.

    ``disabled = true`` is the Lua-side counterpart of Hyprlang's
    ``monitor = OUTPUT, disable`` short-form; we round-trip it back to
    the legacy shape so line-oriented consumers see the canonical form
    regardless of which side wrote it.
    """
    if t.get("disabled") is True:
        return f"{t.get('output', '')}, disable"
    parts = [
        str(t.get("output", "")),
        str(t.get("mode", "preferred")),
        str(t.get("position", "auto")),
        format_number(t.get("scale", 1)),
    ]
    excluded = {"output", "mode", "position", "scale", "disabled"}
    extras = {k: v for k, v in t.items() if k not in excluded}
    for key in sorted(extras):
        parts.append(key)
        parts.append(scalar_to_hyprlang(extras[key]))
    return ", ".join(parts)


def bezier_value(name: Any, t: dict[str, Any]) -> str:
    """``hl.curve("name", { type=bezier, points={{a,b},{c,d}} })`` → ``name, a, b, c, d``."""
    points: list[Any] = t.get("points", [])
    flat: list[str] = [str(name)]
    for pair in points:
        if isinstance(pair, list):
            flat.extend(scalar_to_hyprlang(p) for p in pair)
    return ", ".join(flat)


def animation_value(t: dict[str, Any]) -> str:
    """``hl.animation({leaf, enabled, speed, bezier, style})`` → CSV form."""
    parts = [str(t.get("leaf", ""))]
    enabled = t.get("enabled", True)
    # Canonical Hyprlang booleans are ``true``/``false`` (project-wide
    # convention since v0.4.5); animation also accepts ``1``/``0`` and a
    # handful of synonyms, but we emit the canonical token.
    parts.append("true" if enabled else "false")
    speed = t.get("speed")
    if speed is not None:
        parts.append(format_number(speed))
    bezier = t.get("bezier") or t.get("spring") or "default"
    parts.append(str(bezier))
    if t.get("style"):
        parts.append(str(t["style"]))
    return ", ".join(parts)


def rule_value(t: dict[str, Any]) -> str:
    """Reassemble window/layer rule tables into modern v3 Hyprlang syntax.

    Action and matcher keys are emitted in sorted order so the output is
    independent of dict-insertion order (the wrapper preserves it from the
    user's Lua, which is unstable across edits).
    """
    match = t.get("match") if isinstance(t.get("match"), dict) else None
    action_parts: list[str] = []
    for key in sorted(t):
        if key == "match":
            continue
        rendered = _render_rule_action(key, t[key])
        if rendered is not None:
            action_parts.append(rendered)

    tokens: list[str] = action_parts
    if match:
        for mkey in sorted(match):
            mvalue = match[mkey]
            tokens.append(f"match:{mkey} {scalar_to_hyprlang(mvalue)}")
    return ", ".join(tokens)


def _render_rule_action(name: str, value: Any) -> str | None:
    # Hyprland 0.53+ rejects bare boolean effects — ``float`` alone fails,
    # ``float on`` succeeds. Always emit the explicit token (see
    # ``_V3_BOOL_EFFECTS`` in ``_migrate.py`` for the same constraint going
    # the other way).
    if value is True:
        return f"{name} on"
    if value is False:
        return f"{name} off"
    return f"{name} {scalar_to_hyprlang(value)}"


def workspace_value(t: dict[str, Any]) -> str:
    """``hl.workspace_rule({workspace, monitor, default, ...})`` → Hyprlang CSV.

    Field names and a handful of boolean senses differ between the two
    forms (Lua ``no_border = true`` ↔ Hyprlang ``border:false``);
    :func:`lua_field_to_hyprlang` carries the catalogue. Unknown keys
    pass through unchanged so plugin / future-Hyprland fields survive
    the round-trip.
    """
    ws = t.get("workspace", "")
    parts = [scalar_to_hyprlang(ws)]
    for key in sorted(t):
        if key == "workspace":
            continue
        name, value = lua_field_to_hyprlang(key, t[key])
        parts.append(f"{name}:{value}")
    return ", ".join(parts)


def gesture_value(t: dict[str, Any]) -> str:
    """``hl.gesture({fingers, direction, action, ...})`` → CSV form."""
    fingers = t.get("fingers", 0)
    direction = t.get("direction", "")
    action = t.get("action", "")
    parts = [format_number(fingers), str(direction), str(action)]
    for key in sorted(t):
        if key in {"fingers", "direction", "action"}:
            continue
        parts.append(f"{key}:{scalar_to_hyprlang(t[key])}")
    return ", ".join(parts)


def emit_device(doc: Document, t: dict[str, Any], *, source: str = "") -> None:
    """``hl.device({name, ...})`` → emit a Hyprlang ``device { ... }`` block.

    The Document model already understands the section form, and that
    keeps the round-trip with the emitter symmetric. A regular CSV-style
    keyword wouldn't work because Hyprlang has no scalar-form ``device``.
    """
    doc.lines.append(SectionOpen(raw="device {\n", name="device", source_name=source))
    for key in sorted(t):
        value = t[key]
        rendered = scalar_to_hyprlang(value)
        doc.lines.append(
            Assignment(
                raw=f"    {key} = {rendered}\n",
                key=key,
                value=rendered,
                full_key=f"device:{key}",
                source_name=source,
            )
        )
    doc.lines.append(SectionClose(raw="}\n", source_name=source))
