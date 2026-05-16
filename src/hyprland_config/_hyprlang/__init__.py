"""Hyprlang format I/O — parser, serializer, and format-specific helpers.

Owns everything that knows about the Hyprlang ``.conf`` text format:

- :mod:`._parser` — text → :class:`~hyprland_config._core._model.Document`
- :mod:`._serializer` — :class:`~hyprland_config._core._model.Document` → text
- :mod:`._source` — ``source = …`` path resolution and cycle detection
- :mod:`._bind` — bind-line text parsing (``bind = MODS, KEY, …``)

The :mod:`hyprland_config._lua` package consumes the same
:class:`Document` AST as input/output, so both formats share one
in-memory representation. Format-agnostic version migrations live in
:mod:`hyprland_config._migrate`.
"""

from hyprland_config._hyprlang._bind import is_bind_keyword, parse_bind_line
from hyprland_config._hyprlang._parser import (
    ParseError,
    is_keyword,
    parse_file,
    parse_string,
)
from hyprland_config._hyprlang._serializer import serialize_hyprlang
from hyprland_config._hyprlang._source import SourceCycleError

__all__ = [
    "ParseError",
    "SourceCycleError",
    "is_bind_keyword",
    "is_keyword",
    "parse_bind_line",
    "parse_file",
    "parse_string",
    "serialize_hyprlang",
]
