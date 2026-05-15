"""Hyprland Lua emitter — serializes Document trees to ``.lua`` configs.

Public surface lives here; the work is split across focused modules:

- :mod:`._format` — value coercion, Lua source formatting, ``hyprctl``
  keyword shell-out translation, dot-prefix convention.
- :mod:`._dispatchers` — Hyprlang dispatcher → ``hl.dsp.*`` mapping table
  and :func:`translate_dispatcher`.
- :mod:`._keywords` — per-keyword emitters (env, monitor, bezier,
  animation, gesture, permission, plugin, exec-block formatter).
- :mod:`._bind` — bind family (``bind`` / ``binde`` / ``bindm`` / …).
- :mod:`._rules` — windowrule / layerrule / workspace rule emitters.
- :mod:`._walker` — Document walker that assembles the full ``.lua`` output.
- :mod:`._public` — single-line emit APIs for live-apply (``hyprctl eval``).
"""

from hyprland_config._lua._emit._public import (
    define_submap_to_lua,
    dispatch_to_lua,
    emit_keyword_line,
    emit_option_assignment,
)
from hyprland_config._lua._emit._walker import LuaFile, serialize_lua, serialize_lua_tree

__all__ = [
    "LuaFile",
    "define_submap_to_lua",
    "dispatch_to_lua",
    "emit_keyword_line",
    "emit_option_assignment",
    "serialize_lua",
    "serialize_lua_tree",
]
