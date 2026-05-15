"""Format-agnostic core: the Document AST plus shared utilities.

The :class:`Document` tree is the canonical in-memory representation of a
Hyprland configuration. The :mod:`hyprland_config._hyprlang` and
:mod:`hyprland_config._lua` packages each translate their respective
on-disk format to and from this AST.
"""

from hyprland_config._core._bind import BIND_FLAG_MAP, BindData
from hyprland_config._core._expr import (
    ExprError,
    evaluate_expression,
    expand_expressions,
    expand_value,
)
from hyprland_config._core._model import (
    Assignment,
    BlankLine,
    Comment,
    Conditional,
    Document,
    ErrorLine,
    KeyValueLine,
    Keyword,
    Line,
    SectionClose,
    SectionOpen,
    Source,
    Variable,
)
from hyprland_config._core._types import (
    Color,
    Gradient,
    Vec2,
    normalize_gradient_string,
    parse_version,
)
from hyprland_config._core._values import coerce_config_value, value_to_conf
from hyprland_config._core._writer import atomic_write

__all__ = [
    "BIND_FLAG_MAP",
    "Assignment",
    "BindData",
    "BlankLine",
    "Color",
    "Comment",
    "Conditional",
    "Document",
    "ErrorLine",
    "ExprError",
    "Gradient",
    "KeyValueLine",
    "Keyword",
    "Line",
    "SectionClose",
    "SectionOpen",
    "Source",
    "Variable",
    "Vec2",
    "atomic_write",
    "coerce_config_value",
    "evaluate_expression",
    "expand_expressions",
    "expand_value",
    "normalize_gradient_string",
    "parse_version",
    "value_to_conf",
]
