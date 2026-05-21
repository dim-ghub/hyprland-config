"""Tiny line-rewrite helpers shared by the per-version migrations.

Lives in its own module so the migration runner and the windowrule
transforms can both import the helpers without a circular dependency.
"""

from collections.abc import Callable

from hyprland_config._core._model import Document, KeyValueLine


def transform_lines(
    doc: Document,
    predicate: Callable[[KeyValueLine], bool],
    transform: Callable[[KeyValueLine], None],
) -> bool:
    """Apply a transform to matching key-value lines. Returns True if any changed."""
    changed = False
    for line in doc.lines:
        if isinstance(line, KeyValueLine) and predicate(line):
            transform(line)
            changed = True
    if changed:
        doc.mark_dirty()
    return changed
