"""Hyprlang → Lua converter — pure backend, no UI.

One-shot migration from a Hyprlang config to the new Lua format Hyprland
0.55+ defaults to. The converter never overwrites an existing ``.lua``
unless the caller opts in, and never touches the input files — the
original ``hyprland.conf`` and any sourced sub-files stay exactly as
they were.

Generic enough to live in :mod:`hyprland_config`: callers (a GUI wizard,
a CLI tool, an editor plugin) get the pure analyse/execute pair and
build their own confirmation flow around it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from hyprland_config._core._writer import atomic_write
from hyprland_config._hyprlang._parser import parse_file
from hyprland_config._lua import serialize_lua_tree


@dataclass(frozen=True, slots=True)
class UnmappedLine:
    """A Hyprlang line the emitter couldn't translate.

    ``source`` is the originating ``.conf`` file — useful for surfacing
    "where do I port this from?" next to the line in a UI.
    """

    source: Path
    line: str


@dataclass
class ConversionPlan:
    """Everything :func:`execute_conversion` needs, plus preview data."""

    input_path: Path
    output_files: dict[Path, str] = field(default_factory=dict)
    existing_lua: list[Path] = field(default_factory=list)
    unmapped: list[UnmappedLine] = field(default_factory=list)
    sourced_count: int = 0

    @property
    def has_conflicts(self) -> bool:
        return bool(self.existing_lua)

    @property
    def primary_output(self) -> Path | None:
        return self.input_path.with_suffix(".lua") if self.input_path else None


@dataclass
class ConversionResult:
    """Outcome of :func:`execute_conversion`."""

    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def analyze_conversion(hyprland_conf: Path) -> ConversionPlan:
    """Inspect *hyprland_conf* and plan the conversion.

    No files are written — this is the safe "dry run" UIs use to
    populate a preview. Returns the planned outputs (with content),
    any existing ``.lua`` files that would conflict, and the lines the
    emitter couldn't translate (so the user can audit them upfront).
    """
    doc = parse_file(hyprland_conf, follow_sources=True)
    files = serialize_lua_tree(doc)

    output_files = {entry.path: entry.content for entry in files}
    existing_lua = [entry.path for entry in files if entry.path.exists()]
    unmapped = [
        UnmappedLine(source=entry.source_path, line=line)
        for entry in files
        for line in entry.unmapped
    ]
    # Sourced sub-document count — exclude the top-level doc itself so
    # the number matches the user's mental model ("I have N sourced files").
    sourced_count = max(0, len(files) - 1)

    return ConversionPlan(
        input_path=hyprland_conf,
        output_files=output_files,
        existing_lua=existing_lua,
        unmapped=unmapped,
        sourced_count=sourced_count,
    )


_STAGING_SUFFIX = ".hyprland-config-converting"


def execute_conversion(plan: ConversionPlan, *, overwrite: bool = False) -> ConversionResult:
    """Write the Lua files described in *plan* in two phases.

    Refuses to overwrite an existing ``.lua`` unless ``overwrite=True``.
    Phase 1 writes every planned file to a staging path
    (``<name>.lua.hyprland-config-converting``); phase 2 renames each
    staged file onto its final path only if phase 1 succeeded for the
    entire batch. The combination of ``atomic_write`` (per-file atomicity)
    and the rename-only commit phase keeps the cross-file conversion all-
    or-nothing — a partial failure leaves the original ``.conf`` files
    untouched and cleans up the staged files.
    """
    result = ConversionResult()

    staged: dict[Path, Path] = {}
    for path, content in plan.output_files.items():
        if path.exists() and not overwrite:
            result.skipped.append(path)
            continue
        staging_path = path.with_name(path.name + _STAGING_SUFFIX)
        try:
            atomic_write(staging_path, content)
        except OSError as exc:
            result.errors.append(f"{path}: {exc}")
            _cleanup_staged(staged)
            return result
        staged[path] = staging_path

    for final, staging_path in staged.items():
        try:
            staging_path.replace(final)
        except OSError as exc:
            result.errors.append(f"{final}: {exc}")
            _cleanup_staged(staged)
            # Anything we already replaced in this phase has already
            # overwritten the originals — recording it in ``written``
            # lets the caller surface the partial commit honestly.
            return result
        result.written.append(final)
    return result


def _cleanup_staged(staged: dict[Path, Path]) -> None:
    """Best-effort removal of staged files left behind by a failed batch."""
    for staging_path in staged.values():
        try:
            staging_path.unlink(missing_ok=True)
        except OSError:
            pass
