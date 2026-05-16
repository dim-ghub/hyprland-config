"""Bracket-aware splitter for Hyprland keyword bodies.

Window-rule and layer-rule bodies are comma-separated lists where individual
tokens can embed commas inside parens, brackets, or braces — matcher regexes
like ``^(rofi|wofi)$`` and effect expressions like ``cursor_x-(window_w*0.5)``
both rely on this. A naive ``str.split(",")`` mangles those tokens; this
splitter preserves them.
"""


def split_top_level(s: str) -> list[str]:
    """Split *s* on commas that aren't inside ``()`` / ``[]`` / ``{}``.

    Empty pieces are dropped; surrounding whitespace is trimmed.
    """
    result: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            if depth > 0:
                depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                result.append(piece)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        result.append(tail)
    return result
