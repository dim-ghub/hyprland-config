"""Reverse-map a recorded ``hl.dsp.*`` dispatcher back to Hyprlang ``(name, arg)``.

Inverse of :mod:`hyprland_config._lua._dispatchers`. The forward emitter
is the source of truth for what we generate; here we cover the same
symbols going the other way, with a passthrough fallback so bind lines
referring to dispatchers we haven't mapped yet still survive the
round-trip in some form.
"""

from typing import Any

from hyprland_config._lua._emit._dispatchers import DIR_MAP
from hyprland_config._lua._read._config import scalar_to_hyprlang

# Dispatcher dotted name → fixed ``(hyprlang_name, arg)`` pair, for
# dispatchers that take no Lua args. ``group.next``/``group.prev`` carry
# the directional letter as their Hyprlang arg.
_SIMPLE_DSP_NAMES: dict[str, tuple[str, str]] = {
    "exit": ("exit", ""),
    "window.close": ("killactive", ""),
    "window.kill": ("forcekillactive", ""),
    "window.pseudo": ("pseudo", ""),
    "window.center": ("centerwindow", ""),
    "window.pin": ("pin", ""),
    "window.fullscreen": ("fullscreen", ""),
    "window.cycle_next": ("cyclenext", ""),
    "window.drag": ("movewindow", ""),
    "window.resize": ("resizewindow", ""),
    "window.bring_to_top": ("bringactivetotop", ""),
    "group.toggle": ("togglegroup", ""),
    "group.next": ("changegroupactive", "f"),
    "group.prev": ("changegroupactive", "b"),
    "force_renderer_reload": ("forcerendererreload", ""),
    "no_op": ("noop", ""),
}

# Single-letter direction values used by movefocus / movewindow /
# moveintogroup — inverse of ``_lua.DIR_MAP`` (l/r/u/d → long form).
_DIRECTION_TO_HYPRLANG = {long: short for short, long in DIR_MAP.items()}


def dispatcher_to_hyprlang(dispatcher: Any) -> tuple[str, str]:
    """Reverse-map a recorded ``hl.dsp.*`` dispatcher back to Hyprlang.

    Returns ``(dispatcher_name, arg_string)``. Falls back to a passthrough
    that preserves the original ``hl.dsp.NAME`` form when we don't have a
    specific mapping — that keeps the bind line round-trip-able even for
    dispatchers the forward emitter never wrote.
    """
    if not isinstance(dispatcher, dict) or "__dsp" not in dispatcher:
        return ("", "")
    name = dispatcher["__dsp"]
    args = dispatcher.get("args", [])

    if name in _SIMPLE_DSP_NAMES:
        return _SIMPLE_DSP_NAMES[name]

    # ``hl.dsp.workspace(N)`` — namespace-as-function shorthand. The
    # wrapper records this as ``__dsp = "workspace"`` with the workspace
    # id (or "e+1" / "name:foo") as the first arg. Hyprlang spelling is
    # ``workspace, <arg>``.
    if name == "workspace":
        return ("workspace", scalar_to_hyprlang(args[0]) if args else "")
    if name == "exec_cmd":
        cmd = args[0] if args else ""
        return ("exec", str(cmd))
    if name == "submap":
        return ("submap", str(args[0]) if args else "")
    if name == "layout":
        return ("layoutmsg", str(args[0]) if args else "")
    if name == "focus":
        return _focus(args[0] if args else None)
    if name == "window.move":
        return _window_move(args[0] if args else None)
    if name == "window.swap":
        return _window_swap(args[0] if args else None)
    if name == "window.float":
        return _window_float(args[0] if args else None)
    if name == "workspace.toggle_special":
        return ("togglespecialworkspace", str(args[0]) if args else "")
    if name == "workspace.move":
        return _workspace_move(args[0] if args else None)
    if name == "window.set_prop":
        return _set_prop(args[0] if args else None)
    if name == "window.alter_zorder":
        return _alter_zorder(args[0] if args else None)
    if name == "window.tag":
        if args and isinstance(args[0], dict):
            return ("tagwindow", str(args[0].get("tag", "")))
        return ("tagwindow", "")
    if name == "window.fullscreen_state":
        return _fullscreen_state(args[0] if args else None)

    # Fall through: keep the Lua-side dotted name so downstream tooling
    # can still display *something* useful and a future revision can
    # extend the table.
    return (name, ", ".join(scalar_to_hyprlang(a) for a in args))


def _fullscreen_state(arg: Any) -> tuple[str, str]:
    """Lua: ``hl.dsp.window.fullscreen_state({internal, client, window?})`` →
    Hyprlang: ``fullscreenstate, INTERNAL CLIENT[,address:0x…]``.
    """
    if not isinstance(arg, dict):
        return ("fullscreenstate", "")
    internal = arg.get("internal")
    client = arg.get("client")
    if internal is None or client is None:
        return ("fullscreenstate", "")
    body = f"{scalar_to_hyprlang(internal)} {scalar_to_hyprlang(client)}"
    window = arg.get("window")
    if window:
        body = f"{body},{window}"
    return ("fullscreenstate", body)


def _focus(arg: Any) -> tuple[str, str]:
    if not isinstance(arg, dict):
        return ("focus", "")
    if "direction" in arg:
        return ("movefocus", _DIRECTION_TO_HYPRLANG.get(arg["direction"], str(arg["direction"])))
    if "workspace" in arg:
        return ("workspace", scalar_to_hyprlang(arg["workspace"]))
    if "monitor" in arg:
        return ("focusmonitor", str(arg["monitor"]))
    if "window" in arg:
        return ("focuswindow", str(arg["window"]))
    if arg.get("last"):
        return ("focuscurrentorlast", "")
    if arg.get("urgent_or_last"):
        return ("focusurgentorlast", "")
    return ("focus", "")


def _window_move(arg: Any) -> tuple[str, str]:
    if not isinstance(arg, dict):
        return ("movewindow", "")
    if "direction" in arg:
        return ("movewindow", _DIRECTION_TO_HYPRLANG.get(arg["direction"], str(arg["direction"])))
    if "workspace" in arg:
        ws = scalar_to_hyprlang(arg["workspace"])
        if arg.get("silent"):
            return ("movetoworkspacesilent", ws)
        return ("movetoworkspace", ws)
    if "into_group" in arg:
        direction = arg["into_group"]
        return ("moveintogroup", _DIRECTION_TO_HYPRLANG.get(direction, str(direction)))
    if "out_of_group" in arg:
        return ("moveoutofgroup", "")
    # ``hl.dsp.window.move({x, y, exact = true})`` → ``movewindowpixel exact X Y``;
    # the relative-pixel form has no documented Hyprlang spelling (the forward
    # emitter doesn't produce it), so any ``{x, y}`` without ``exact`` is
    # surfaced as the literal Lua name and lets the caller decide.
    if "x" in arg and "y" in arg:
        coords = f"{scalar_to_hyprlang(arg['x'])} {scalar_to_hyprlang(arg['y'])}"
        if arg.get("relative") is False:
            return ("movewindowpixel", f"exact {coords}")
        return ("window.move", coords)
    return ("movewindow", "")


def _window_swap(arg: Any) -> tuple[str, str]:
    if not isinstance(arg, dict):
        return ("swapwindow", "")
    if "direction" in arg:
        return ("swapwindow", _DIRECTION_TO_HYPRLANG.get(arg["direction"], str(arg["direction"])))
    if arg.get("next"):
        return ("swapnext", "")
    return ("swapwindow", "")


def _window_float(arg: Any) -> tuple[str, str]:
    if isinstance(arg, dict):
        action = arg.get("action", "toggle")
        if action == "set":
            return ("setfloating", "")
        if action == "unset":
            return ("settiled", "")
    return ("togglefloating", "")


def _workspace_move(arg: Any) -> tuple[str, str]:
    if not isinstance(arg, dict):
        return ("workspace.move", "")
    if "workspace" in arg:
        ws = scalar_to_hyprlang(arg["workspace"])
        mon = scalar_to_hyprlang(arg.get("monitor", ""))
        return ("moveworkspacetomonitor", f"{ws} {mon}")
    if "monitor" in arg:
        return ("movecurrentworkspacetomonitor", str(arg["monitor"]))
    return ("workspace.move", "")


def _set_prop(arg: Any) -> tuple[str, str]:
    if not isinstance(arg, dict):
        return ("setprop", "")
    prop = str(arg.get("prop", ""))
    value = scalar_to_hyprlang(arg.get("value", ""))
    window = arg.get("window")
    if window:
        return ("setprop", f"{window} {prop} {value}")
    return ("setprop", f"{prop} {value}")


def _alter_zorder(arg: Any) -> tuple[str, str]:
    if isinstance(arg, dict) and "mode" in arg:
        return ("alterzorder", str(arg["mode"]))
    return ("alterzorder", "")
