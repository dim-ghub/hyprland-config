"""Hyprlang bind-line text parsing — recognise and decompose ``bind = …`` lines."""

import re

from hyprland_config._core._bind import BindData

# Hyprland generates bind variants combinatorially from flag chars
# (e, l, r, n, m, t, i, s, d, p). A regex covers all current and future
# combinations without needing manual enumeration.
_BIND_RE = re.compile(r"^bind[elnrmtisdp]*$")


def is_bind_keyword(name: str) -> bool:
    """Return True if *name* is a bind-variant keyword (bind, binde, bindm …)."""
    return _BIND_RE.match(name) is not None


def parse_bind_line(line: str) -> BindData | None:
    """Parse a ``'bind = MODS, KEY, dispatcher, arg'`` line.

    Returns ``None`` if the line's keyword is not a bind variant
    (``bind``, ``binde``, ``bindm``, …) or if it has fewer than the
    three required comma-separated parts after ``=``.
    """
    if "=" not in line:
        return None
    btype, _, rest = line.partition("=")
    btype = btype.strip()
    if not is_bind_keyword(btype):
        return None
    parts = [p.strip() for p in rest.split(",", 3)]
    if len(parts) < 3:
        return None
    mods_str = parts[0]
    key = parts[1]
    dispatcher = parts[2]
    # A trailing comma in the bind line (``MODS, KEY, killactive,``) carries
    # no meaning — it represents an empty fifth field that got absorbed into
    # the arg slot when we split with ``maxsplit=3``. Strip it so dispatchers
    # don't receive ``"togglesplit,"`` as their arg.
    arg = parts[3].rstrip(",").strip() if len(parts) > 3 else ""
    mods = mods_str.split() if mods_str else []
    return BindData(
        bind_type=btype,
        mods=mods,
        key=key,
        dispatcher=dispatcher,
        arg=arg,
    )
