"""Document → Hyprlang text serializer.

The :class:`Document` AST stores each line's already-rendered Hyprlang
text on its ``raw`` field — both the parser and the Lua reader populate
``raw`` with Hyprlang-formatted text so consumers see one canonical
shape. Serialization is the trivial join of those raw strings; the
function lives in this package to make the format ownership explicit
and to mirror :func:`hyprland_config.serialize_lua`.
"""

from hyprland_config._core._model import Document


def serialize_hyprlang(doc: Document) -> str:
    """Reconstruct *doc*'s Hyprlang source text from its line nodes."""
    return "".join(line.raw for line in doc.lines)
