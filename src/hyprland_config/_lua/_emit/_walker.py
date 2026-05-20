"""Document → Lua emitter — the bulk walker.

Walks a :class:`Document` and produces Lua source by routing each line
to the right per-keyword emitter (see :mod:`._keywords`, :mod:`._bind`,
:mod:`._rules`). The per-section state machine handles ``device { … }``,
``windowrule { … }``, and ``layerrule { … }`` blocks which collect their
contents across multiple input lines.

Coverage:

- ``section:key = value`` → merged into one ``hl.config({...})`` call,
  with nested tables for both colon (``section:sub:key``) and dot
  (``section:col.inactive_border``) separators.
- ``env``, ``monitor``, ``bezier``, ``animation`` → dedicated calls
  (``hl.env``, ``hl.monitor``, ``hl.curve``, ``hl.animation``).
- ``bind`` family (``bind``, ``binde``, ``bindm``, …) → ``hl.bind(KEY,
  hl.dsp.*, FLAGS)``.
- ``windowrule`` / ``windowrulev2`` / ``layerrule`` / ``workspace`` /
  ``gesture`` / ``permission`` → matching ``hl.*`` calls.
- ``device { … }`` section → ``hl.device({...})``.
- ``exec`` → top-level ``hl.exec_cmd(...)`` (Lua re-evaluates the file on
  every reload, matching Hyprlang ``exec`` semantics). ``exec-once`` is
  batched into one ``hl.on("hyprland.start", function() … end)`` block
  whose callback only fires at session startup; ``exec-shutdown`` lands
  in the matching ``hyprland.shutdown`` block.
- ``exec, hyprctl keyword <section>:<option> <value>`` (in a bind or at
  top level) → ``hl.config({...})``: Lua-mode Hyprland rejects the
  ``keyword`` IPC verb, so the shell-out would silently break post-
  migration. Bind dispatchers become a closure
  (``function() hl.config({...}) end``); top-level ``exec`` execs become
  a bare ``hl.config({...})`` call, while ``exec-once``/``exec-shutdown``
  versions nest inside their ``hl.on`` block.
- ``submap = NAME`` … ``submap = reset`` blocks → one
  ``hl.define_submap(NAME, function() <hl.bind…> end)`` call; binds
  inside the range get scoped to the named submap instead of leaking
  to the global keymap.

Anything we can't translate confidently (an unmapped dispatcher, a malformed
rule, a plugin we don't recognise) lands at the bottom in a trailing
manual-conversion block so users can see exactly what wasn't migrated.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hyprland_config._core._model import (
    Assignment,
    Comment,
    Conditional,
    Document,
    Keyword,
    Line,
    Rule,
    SectionClose,
    SectionOpen,
    Source,
    Variable,
)
from hyprland_config._core._rules import LAYER_BOOL_EFFECTS, V3_BOOL_EFFECTS
from hyprland_config._hyprlang._bind import is_bind_keyword
from hyprland_config._lua._emit._bind import emit_bind
from hyprland_config._lua._emit._conditional import translate_expression
from hyprland_config._lua._emit._dispatchers import (
    rewrite_hyprctl_dispatch_in_shell,
    translate_dispatcher,
)
from hyprland_config._lua._emit._format import (
    INDENT,
    coerce_value,
    emit_exec_cmd_call,
    emit_keyword_config_call,
    expand_value_lua,
    format_table,
    format_value,
    lua_var_name,
    parse_hyprctl_dispatch,
    parse_hyprctl_keyword,
    quote_string,
    set_nested,
    split_key,
)
from hyprland_config._lua._emit._keywords import format_exec_block
from hyprland_config._lua._emit._public import STATIC_KEYWORD_EMITTERS
from hyprland_config._lua._emit._rules import add_block_rule_field


@dataclass
class _Group:
    """One topical chunk of output, delimited by Comment lines in the source.

    The walker opens a fresh group whenever it sees a Comment, so a Hyprlang
    config like ``# Keybinds\\nbind = …`` emits the Lua bind calls under a
    ``-- Keybinds`` header. Groups carry their own config_tree / extras /
    exec buckets — assignments don't merge across topical boundaries even
    when they could (last-write-wins still applies; values just live in
    separate ``hl.config({...})`` calls).
    """

    header: str | None = None
    config_tree: dict[str, Any] = field(default_factory=dict)
    extras: list[str] = field(default_factory=list)
    # Startup-only (``exec-once``) and shutdown-only (``exec-shutdown``)
    # commands collect here so the assembler can wrap each list in one
    # ``hl.on(event, function() … end)`` block. ``exec`` (every-reload)
    # entries skip these buckets and go straight into ``extras`` — they
    # belong at top-level so file re-evaluation on reload re-runs them.
    exec_once: list[str] = field(default_factory=list)
    exec_shutdown: list[str] = field(default_factory=list)


@dataclass
class _SubmapScope:
    """An open ``submap = NAME`` block being collected.

    Hyprlang submaps run from a ``submap = NAME`` declaration until the next
    ``submap = reset`` (or the end of the document). Every bind in that
    range belongs inside ``hl.define_submap(NAME, function() … end)``; the
    rendered ``hl.bind(...)`` strings accumulate in ``body`` and get wrapped
    when the closing ``reset`` fires.
    """

    name: str
    body: list[str] = field(default_factory=list)


@dataclass
class _CondBranch:
    """One branch of a translated conditional block.

    ``lua_expr`` is the Lua source for the branch's condition, or ``None``
    for an ``else`` branch. ``lines`` buffers the body until the matching
    ``endif`` fires — at that point the body is re-walked through a fresh
    sub-state to produce the Lua statements that go inside the branch.
    ``boundary`` holds the directive line that opened the branch so the
    untranslatable-fallback path can re-emit the whole block verbatim.
    """

    lua_expr: str | None
    boundary: Conditional | None = None
    lines: list[Line] = field(default_factory=list)


@dataclass
class _CondScope:
    """A single ``# hyprlang if … endif`` block being collected.

    ``branches`` grows as ``elif``/``else`` arrive; the last entry is the
    one currently accumulating lines. ``depth`` counts nested ``if`` blocks
    buffered into the current branch — we only consume a directive at depth
    zero, so a nested ``endif`` doesn't accidentally close the outer scope.
    ``untranslatable`` flips when any branch's expression can't be mapped
    to Lua; the whole block then surfaces verbatim in the manual-conversion
    list instead of producing wrong Lua.
    """

    branches: list[_CondBranch] = field(default_factory=list)
    depth: int = 0
    untranslatable: bool = False


@dataclass
class _EmitState:
    """Accumulator for everything we've emitted while walking the document.

    ``groups`` always holds at least one group — the leading unnamed group
    that collects content before any comment is seen. ``skipped`` is global
    rather than per-group because the trailing manual-conversion block is
    one list for the whole file, not per topical section.

    ``cond_stack`` tracks active ``# hyprlang if`` scopes for buffering
    until each matching ``endif``. ``referenced_vars`` collects ``$VAR``
    names that appear in conditional expressions so :func:`_assemble_lua`
    can emit ``local NAME = "value"`` declarations at the top of the
    output — the rest of the emitter still inline-expands ``$VAR`` at use
    sites, so this only surfaces variables that the conditional logic
    actually needs at Lua runtime.
    """

    groups: list[_Group] = field(default_factory=lambda: [_Group()])
    skipped: list[str] = field(default_factory=list)
    section_stack: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)
    cond_stack: list[_CondScope] = field(default_factory=list)
    submap: _SubmapScope | None = None
    referenced_vars: dict[str, str] = field(default_factory=dict)

    @property
    def current(self) -> _Group:
        return self.groups[-1]

    def open_group(self, header: str) -> None:
        self.groups.append(_Group(header=header))


@dataclass(frozen=True, slots=True)
class LuaFile:
    """One ``.lua`` file produced by :func:`serialize_lua_tree`.

    ``path`` is the resolved output ``.lua`` path; ``source_path`` is
    the originating ``.conf`` (the input to the emitter). ``unmapped``
    lists the original Hyprlang lines from this file that the emitter
    couldn't translate — they're absent from ``content`` (other than
    as the trailing manual-conversion comment block) and the user needs
    to port them by hand.
    """

    path: Path
    source_path: Path
    content: str
    unmapped: list[str]


def serialize_lua(doc: Document, *, emit_migration_markers: bool = True) -> str:
    """Render *doc* as a single Lua config string.

    Walks the document in Hyprland's evaluation order — ``source = …``
    directives are inlined at their position so a multi-file config emits
    as one Lua document, matching how Hyprland resolves it at runtime.
    Returns a string ending in a newline; an empty document returns an
    empty string. The library does not stamp its own ``-- Generated by``
    banner — consumers brand their output via Comment nodes if they want
    one.

    ``emit_migration_markers`` is accepted for backwards compatibility but
    is now a no-op. It used to control the ``-- TODO: was exec-once``
    suffix on translated ``exec-once`` shell commands; that hint existed
    because the emitter wrapped both ``exec`` and ``exec-once`` in
    ``hl.on("hyprland.start", …)`` and lost the distinction. The emitter
    now keeps the two apart (``exec`` emits at top level, ``exec-once``
    in the ``hl.on`` block), so the marker isn't needed.

    Use :func:`serialize_lua_tree` instead when you want to preserve the
    original ``hyprland.conf.d/*.conf`` split as separate ``.lua`` files
    bridged by ``dofile()`` calls.
    """
    del emit_migration_markers
    state = _EmitState()
    for owning_doc, line in doc.iter_lines(recursive=True):
        _process_line(line, state, owning_doc)
    return _assemble_lua(state)


def serialize_lua_tree(doc: Document, *, emit_migration_markers: bool = True) -> list[LuaFile]:
    """Emit one Lua file per parsed sub-document, mirroring source structure.

    Returns one :class:`LuaFile` per document reached via ``source = …``;
    each carries the resolved output path (``.conf`` swapped for ``.lua``,
    ``X.conf.d`` directories remapped to ``X.lua.d``), the rendered
    content, and the list of original lines that didn't translate. The
    parent's content gets ``dofile("…/foo.lua")`` calls at the position
    of each ``source`` line.

    Documents without a ``path`` attribute (e.g. ``parse_string`` input)
    are skipped — there's no natural file name to use for them.

    Caveat: each output file's ``hl.config({...})`` block is the merged
    last-wins result of *that file's* assignments. If you depend on a
    parent assignment that comes *after* a ``source`` directive overriding
    the same key in the child, prefer :func:`serialize_lua` so the merge
    happens across the whole tree in evaluation order.

    See :func:`serialize_lua` for the meaning of ``emit_migration_markers``.
    """
    del emit_migration_markers
    output: list[LuaFile] = []
    _emit_doc_tree(doc, output)
    return output


def _emit_doc_tree(doc: Document, output: list[LuaFile]) -> None:
    """Recursively render *doc* and its sourced children into *output*."""
    state = _EmitState()
    for line in doc.lines:
        if isinstance(line, Source):
            for sub_doc in line.documents:
                _emit_doc_tree(sub_doc, output)
                sub_lua_path = _conf_path_to_lua(sub_doc.path)
                if sub_lua_path is not None:
                    state.current.extras.append(f"dofile({quote_string(str(sub_lua_path))})")
            continue
        _process_line(line, state, doc)

    out_path = _conf_path_to_lua(doc.path)
    if out_path is None or doc.path is None:
        return
    output.append(
        LuaFile(
            path=out_path,
            source_path=doc.path,
            content=_assemble_lua(state),
            unmapped=list(state.skipped),
        )
    )


def _conf_path_to_lua(path: Path | None) -> Path | None:
    """Map a Hyprlang config path to its ``.lua`` output path.

    The file's own ``.conf`` suffix becomes ``.lua``. Any parent directory
    whose name ends in ``.conf.d`` — the standard Unix "drop-in include"
    convention Hyprland configs use a lot — also gets remapped to
    ``.lua.d``. This stops Hyprlang configs that wildcard-source the dir
    (``source = ~/.config/hypr/hyprland.conf.d/*``) from picking up the
    new ``.lua`` files and trying to parse them as Hyprlang.
    """
    if path is None:
        return None
    parts = tuple(_remap_d_dir(part) for part in path.parent.parts)
    return Path(*parts) / (path.stem + ".lua")


def _remap_d_dir(name: str) -> str:
    """Translate a single path component: ``X.conf.d`` → ``X.lua.d``."""
    if name.endswith(".conf.d"):
        return name[: -len(".conf.d")] + ".lua.d"
    return name


def _assemble_lua(state: _EmitState) -> str:
    """Render an accumulated :class:`_EmitState` to a Lua source string.

    Each group becomes one section (header line plus its bucket contents);
    sections are joined with a blank line between them. The trailing
    manual-conversion block, when present, sits after every group.

    Variables referenced by translated ``# hyprlang if`` expressions get
    a leading ``local`` preamble so the conditional bodies can read them
    at Lua load time — every other ``$VAR`` reference is inline-expanded
    by the per-line emitters, so this preamble only ever lists variables
    the conditional logic actually needs.
    """
    _drain_open_conditionals(state)
    _close_submap(state)
    sections: list[str] = []
    if state.referenced_vars:
        sections.append(_format_var_preamble(state.referenced_vars))
    for group in state.groups:
        rendered = _render_group(group)
        if rendered is not None:
            sections.append(rendered)
    if state.skipped:
        todo = ["-- TODO: the following entries need manual conversion to Lua:\n"]
        todo.extend(f"--   {entry}\n" for entry in state.skipped)
        sections.append("".join(todo))

    return "\n".join(sections)


def _drain_open_conditionals(state: _EmitState) -> None:
    """Surface any ``# hyprlang if`` block that never reached an ``endif``.

    A well-formed Hyprlang config always closes its conditionals, but lenient
    parsing can produce documents with a trailing ``if`` and no matching
    ``endif`` (cut-off pastes, in-progress edits). Without this drain the
    buffered body lines would silently vanish; the manual-conversion block
    is the right place for them so the user sees the unfinished block.
    """
    while state.cond_stack:
        scope = state.cond_stack.pop()
        for branch in scope.branches:
            if branch.boundary is not None:
                state.skipped.append(branch.boundary.raw.rstrip("\n").strip())
            for body_line in branch.lines:
                state.skipped.append(body_line.raw.rstrip("\n").strip())


def _format_var_preamble(variables: dict[str, str]) -> str:
    """Render ``local var_NAME = …`` lines for each referenced variable.

    Values flow through the same :func:`coerce_value` / :func:`format_value`
    pipeline the inline assignment path uses, so numeric literals emit as
    Lua numbers (``local var_size = 10``), bool words as booleans
    (``local var_enabled = true``), gradients as structured tables, and
    everything else as quoted strings. Values that themselves contain
    ``$other`` references re-expand through the marker pipeline so
    ``$accent = $primary`` emits as ``local var_accent = var_primary``.
    Transitive deps are already in *variables* (the walker registers them
    at scan time).

    Conditional comparisons compensate for the typed locals by wrapping
    string-equality LHS in ``tostring(...)`` — see
    :func:`hyprland_config._lua._emit._conditional.translate_expression`.
    Numeric comparisons keep their pre-existing ``tonumber(...)`` wrap.

    Insertion order is preserved; transitive deps surface after the
    variable that referenced them. Lua tolerates this so long as no
    declaration is read at *initialisation* time — locals are looked up
    when the call site runs, not when the local is declared.
    """
    lines: list[str] = []
    for name, value in variables.items():
        own_refs: dict[str, str] = {}
        rendered_value = expand_value_lua(value, variables, own_refs)
        coerced = coerce_value(rendered_value)
        lines.append(f"local {lua_var_name(name)} = {format_value(coerced, 0)}")
    return "\n".join(lines) + "\n"


def _render_group(group: _Group) -> str | None:
    """Render one group as a section string, or None if it's empty and unheaded.

    A group with a header but no content emits the header line on its own
    (preserves decorative comments and section-only stubs). A group with
    content but no header emits the content alone (the leading unnamed
    group, or any topical block in a comment-free config).
    """
    parts: list[str] = []
    if group.config_tree:
        parts.append(f"hl.config({format_table(group.config_tree, indent=0)})\n")
    if group.extras:
        parts.append("".join(f"{call}\n" for call in group.extras))
    if group.exec_once:
        parts.append(format_exec_block("hyprland.start", group.exec_once))
    if group.exec_shutdown:
        parts.append(format_exec_block("hyprland.shutdown", group.exec_shutdown))

    if not parts and group.header is None:
        return None

    if group.header is None:
        header_line = ""
    elif group.header:
        header_line = f"-- {group.header}\n"
    else:
        header_line = "--\n"
    return header_line + "\n".join(parts)


_BLOCK_RULE_SECTIONS = frozenset({"windowrule", "windowrulev2", "layerrule"})


def _process_line(line: Line, state: _EmitState, owning_doc: Document) -> None:
    """Route a single line to the right accumulator on *state*.

    *owning_doc* is the Document that originally contained *line* (the parent
    when sources are followed). We use its variable scope to expand
    ``$var`` references in keyword arguments before emitting.
    """
    # Inside a ``# hyprlang if … endif`` block, buffer lines into the active
    # branch until the closing directive fires. Nested ``if``/``endif`` pairs
    # count toward ``scope.depth`` so we only consume the directives that
    # actually belong to the current scope; the nested ones travel along as
    # buffered Line nodes and get re-walked through a sub-state when the
    # outer block renders.
    if state.cond_stack:
        scope = state.cond_stack[-1]
        if isinstance(line, Conditional):
            if line.kind == "if":
                scope.depth += 1
                scope.branches[-1].lines.append(line)
                return
            if line.kind == "endif" and scope.depth > 0:
                scope.depth -= 1
                scope.branches[-1].lines.append(line)
                return
            if line.kind in ("elif", "else") and scope.depth > 0:
                scope.branches[-1].lines.append(line)
                return
            if line.kind == "noerror":
                scope.branches[-1].lines.append(line)
                return
            # Fall through: directive at depth 0, addressed by _handle_conditional.
        else:
            scope.branches[-1].lines.append(line)
            return

    if isinstance(line, Conditional):
        _handle_conditional(line, state, owning_doc)
        return

    if isinstance(line, Variable):
        # No standalone output — referenced variables surface in the local
        # preamble (see `_assemble_lua`); unreferenced variables stay inline-
        # expanded at their consumption sites.
        return

    if isinstance(line, Comment):
        # Comments delimit topical groups — open a fresh accumulator so the
        # following lines emit under their own `-- header` and don't merge
        # back into the prior section's hl.config call.
        state.open_group(line.text)
        return

    if isinstance(line, SectionOpen):
        # `device { … }`, `windowrule { … }`, `windowrulev2 { … }`, and
        # `layerrule { … }` blocks all produce a single Lua call when the
        # section closes — we collect their contents into a buffer instead
        # of merging into the general config_tree. Other sections fall
        # through to the normal Assignment full_key handling.
        if line.name == "device":
            buffer: dict[str, Any] = {}
            if line.section_key:
                buffer["name"] = line.section_key
            state.section_stack.append(("device", buffer))
        elif line.name in _BLOCK_RULE_SECTIONS:
            # ``windowrule[my-name] { … }`` / ``layerrule[my-name] { … }``
            # seeds ``name`` from the section key; an inner ``name = …``
            # assignment in the block body overrides it via
            # :func:`add_block_rule_field`'s plain-write semantics.
            buffer = {"name": line.section_key} if line.section_key else {}
            state.section_stack.append((line.name, buffer))
        else:
            state.section_stack.append((line.name, None))
        return

    if isinstance(line, SectionClose):
        if state.section_stack:
            close_name, close_buf = state.section_stack.pop()
            if close_name == "device" and close_buf is not None:
                state.current.extras.append(f"hl.device({format_table(close_buf, indent=0)})")
            elif close_name in ("windowrule", "windowrulev2") and close_buf is not None:
                state.current.extras.append(f"hl.window_rule({format_table(close_buf, indent=0)})")
            elif close_name == "layerrule" and close_buf is not None:
                state.current.extras.append(f"hl.layer_rule({format_table(close_buf, indent=0)})")
        return

    if isinstance(line, Assignment):
        # Substitute ``$var`` references with marker tokens (and register
        # them in ``state.referenced_vars`` for the preamble) so each
        # reference survives downstream coercion as a ``LuaExpr`` pointing
        # at a Lua local. Variables only resolve in their defining doc,
        # so we use ``owning_doc.variables`` rather than the root's.
        value = expand_value_lua(line.value, owning_doc.variables, state.referenced_vars)
        if state.section_stack:
            cur_name, cur_buf = state.section_stack[-1]
            if cur_name == "device" and cur_buf is not None:
                cur_buf[line.key] = coerce_value(value)
                return
            if cur_name in _BLOCK_RULE_SECTIONS and cur_buf is not None:
                add_block_rule_field(cur_buf, line.key, value)
                return
        set_nested(state.current.config_tree, split_key(line.full_key), coerce_value(value))
        return

    if isinstance(line, Keyword):
        if state.section_stack:
            cur_name, cur_buf = state.section_stack[-1]
            if cur_name in _BLOCK_RULE_SECTIONS and cur_buf is not None:
                # Inside a `windowrule { … }` / `windowrulev2 { … }` / `layerrule { … }`
                # block, keywords like ``workspace`` are rule actions, not the
                # top-level keyword (`workspace = 1, monitor:DP-1` defines a
                # workspace rule; the same line inside a windowrule block tells
                # Hyprland to assign matching windows to workspace 1). Route
                # the field into the block buffer instead of falling through
                # to the standalone-keyword emitter.
                add_block_rule_field(cur_buf, line.key, line.value)
                return
        _process_keyword(line, state, owning_doc)
        return

    if isinstance(line, Rule):
        _emit_rule(line, state)


def render_rule_lua(rule: Rule) -> str:
    """Render a structured :class:`Rule` as one ``hl.window_rule({…})``
    / ``hl.layer_rule({…})`` call string.

    Both rule kinds share the same table shape (``name``, ``enabled``,
    ``match``, plus effect fields); only the wrapping function differs.
    Used by the walker for full-document emission and by single-Rule
    consumers (e.g. hyprmod's edit-dialog Lua preview) that need the
    same snippet without standing up a Document.
    """
    table: dict[str, Any] = {}
    if rule.name:
        table["name"] = rule.name
    if not rule.enabled:
        table["enabled"] = False
    if rule.matchers:
        table["match"] = {k: coerce_value(v) for k, v in rule.matchers}
    for name, args in rule.effects:
        table[name] = _effect_value_to_lua(name, args)
    fn = "hl.layer_rule" if rule.kind == "layerrule" else "hl.window_rule"
    return f"{fn}({format_table(table, indent=0)})"


def _emit_rule(rule: Rule, state: _EmitState) -> None:
    """Append :func:`render_rule_lua` output to the active group's extras."""
    state.current.extras.append(render_rule_lua(rule))


def _effect_value_to_lua(name: str, args: str) -> Any:
    """Coerce a Rule's stringly-typed effect args back to Lua-native form.

    Bool effects come in as ``"on"`` / ``"off"`` from the Hyprlang side;
    Lua wants ``true`` / ``false``. Numeric and string args route through
    :func:`coerce_value` so quoted/escaped output matches what the user
    would write by hand. Empty args on a known bool effect default to
    ``true`` (Hyprland's "missing value" interpretation for these names).
    """
    stripped = args.strip()
    if name in V3_BOOL_EFFECTS or name in LAYER_BOOL_EFFECTS:
        if not stripped or stripped.lower() in ("on", "true", "yes", "1"):
            return True
        if stripped.lower() in ("off", "false", "no", "0"):
            return False
    return coerce_value(stripped)


def _try_translate_hyprctl_dispatch(cmd: str) -> str | None:
    """Return the ``hl.dsp.*`` snippet when *cmd* is a single ``hyprctl dispatch``.

    Returns ``None`` if the shape doesn't match (anything more complex than a
    single hyprctl invocation, e.g. ``sleep && hyprctl …``), or if the verb
    has no native translation — leaving the caller to fall back to the
    embedded-rewrite path.
    """
    parsed = parse_hyprctl_dispatch(cmd)
    if parsed is None:
        return None
    return translate_dispatcher(parsed[0], parsed[1])


def _process_keyword(line: Keyword, state: _EmitState, owning_doc: Document) -> None:
    """Route a Keyword line to the right accumulator or fallback bucket."""
    name = line.key
    # Variables like ``$mainMod`` only resolve inside the document that
    # defined them, so we use the owning doc's scope. Marker-substitute
    # so ``$mainMod`` flows downstream as a token referencing the Lua
    # local ``var_mainMod`` rather than the inlined value.
    args = expand_value_lua(line.value, owning_doc.variables, state.referenced_vars)

    # ``submap = NAME`` opens a Hyprlang submap; the binds that follow until
    # the matching ``submap = reset`` belong to it. Lua's ``hl.define_submap``
    # is declarative, so the walker buffers the body until reset (or EOF)
    # then emits the whole ``hl.define_submap(NAME, function() … end)``
    # block as one unit.
    if name == "submap":
        _handle_submap_directive(args, state)
        return

    # ``exec`` re-runs on every reload (the Lua file re-evaluates), so
    # its translation lands in ``extras`` and renders at top level — same
    # height as ``hl.env`` / ``hl.bind`` / etc. ``exec-once`` and
    # ``exec-shutdown`` are event-bound: they batch into their dedicated
    # buckets so the assembler can wrap each list in one
    # ``hl.on(event, function() … end)`` block whose callback fires only
    # on that event.
    if name in ("exec", "exec-once", "exec-shutdown"):
        # Indent depth: ``exec`` writes at top level (no surrounding block),
        # the others nest inside an ``hl.on`` body.
        indent = 0 if name == "exec" else 1
        keyword = parse_hyprctl_keyword(args)
        dispatch_translation = _try_translate_hyprctl_dispatch(args)
        if keyword is not None:
            translated = emit_keyword_config_call(*keyword, indent=indent)
        elif dispatch_translation is not None:
            translated = f"hl.dispatch({dispatch_translation})"
        else:
            translated = emit_exec_cmd_call(rewrite_hyprctl_dispatch_in_shell(args))
        if name == "exec":
            state.current.extras.append(translated)
        elif name == "exec-shutdown":
            state.current.exec_shutdown.append(translated)
        else:
            state.current.exec_once.append(translated)
        return

    if is_bind_keyword(name):
        result = emit_bind(name, args)
        if result is None:
            state.skipped.append(f"{name} = {args}")
        elif state.submap is not None:
            state.submap.body.append(result)
        else:
            state.current.extras.append(result)
        return

    emitter = STATIC_KEYWORD_EMITTERS.get(name)
    if emitter is None:
        # Future or plugin keyword we don't yet translate. Surface the
        # original line in the manual-conversion block instead of silently
        # producing invalid Lua.
        state.skipped.append(f"{name} = {args}")
        return
    result = emitter(args)
    if result is None:
        state.skipped.append(f"{name} = {args}")
    else:
        state.current.extras.append(result)


# ---------------------------------------------------------------------------
# Submap block handling
# ---------------------------------------------------------------------------


def _handle_submap_directive(args: str, state: _EmitState) -> None:
    """Open, switch, or close the current submap scope.

    ``submap = reset`` closes the current scope; any other name opens a new
    one (closing the previous one first if a misbehaving config skipped the
    reset). Empty submaps are dropped on close — Hyprland rejects them with
    "submap with no binds", and emitting an ``hl.define_submap`` that
    registers nothing is just noise.
    """
    target = args.strip()
    if target == "reset":
        _close_submap(state)
        return
    if state.submap is not None:
        _close_submap(state)
    state.submap = _SubmapScope(name=target)


def _close_submap(state: _EmitState) -> None:
    """Finalize the active submap into an ``hl.define_submap(...)`` call."""
    submap = state.submap
    if submap is None:
        return
    state.submap = None
    if not submap.body:
        return
    indented = "\n".join(f"{INDENT}{ln}" for ln in submap.body)
    snippet = f"hl.define_submap({quote_string(submap.name)}, function()\n{indented}\nend)"
    state.current.extras.append(snippet)


# ---------------------------------------------------------------------------
# Conditional directive handling
# ---------------------------------------------------------------------------


def _handle_conditional(line: Conditional, state: _EmitState, owning_doc: Document) -> None:
    """Route a ``# hyprlang`` directive at the current scope's depth zero.

    ``if`` opens a new scope; ``elif`` / ``else`` start a fresh branch on
    the current scope; ``endif`` closes the scope and emits the translated
    block. ``noerror`` has no Lua equivalent and is dropped with an
    explanatory comment in the output so the user can see what got removed.
    Orphan ``elif`` / ``else`` / ``endif`` directives (no matching ``if``)
    land in the manual-conversion block rather than producing broken Lua.
    """
    kind = line.kind
    raw = line.raw.rstrip("\n")

    if kind == "noerror":
        state.current.extras.append(f"-- noerror has no Lua equivalent (was: {raw.strip()})")
        return

    if kind == "if":
        scope = _CondScope()
        _open_branch(scope, line, owning_doc, state)
        state.cond_stack.append(scope)
        return

    if not state.cond_stack:
        state.skipped.append(raw.strip())
        return

    scope = state.cond_stack[-1]

    if kind == "elif":
        _open_branch(scope, line, owning_doc, state)
        return

    if kind == "else":
        scope.branches.append(_CondBranch(lua_expr=None, boundary=line))
        return

    if kind == "endif":
        state.cond_stack.pop()
        if scope.untranslatable:
            # Any branch's expression failed to translate — surface the whole
            # block verbatim (directives plus bodies) so the user can port it
            # by hand instead of getting a partially-correct, silently broken
            # Lua block.
            for branch in scope.branches:
                if branch.boundary is not None:
                    state.skipped.append(branch.boundary.raw.rstrip("\n").strip())
                for body_line in branch.lines:
                    state.skipped.append(body_line.raw.rstrip("\n").strip())
            state.skipped.append(line.raw.rstrip("\n").strip())
            return
        rendered = _emit_conditional_block(scope, state, owning_doc)
        if state.cond_stack:
            # Outer conditional is still collecting — the rendered block goes
            # into the outer branch's buffer as a pre-rendered Line; the
            # sub-walker recognizes ``_RawLua`` and emits the text as-is when
            # the outer block renders.
            outer = state.cond_stack[-1]
            outer.branches[-1].lines.append(_RawLua(raw=rendered))
        else:
            state.current.extras.append(rendered)
        return


def _open_branch(
    scope: _CondScope, directive: Conditional, owning_doc: Document, state: _EmitState
) -> None:
    """Start a new ``if``/``elif`` branch with a translated expression.

    Pulls every ``$VAR`` named in the expression into ``state.referenced_vars``
    so the preamble can declare them as Lua ``local``\\ s. An expression
    we can't translate (compound boolean, unknown shape) flips the scope's
    ``untranslatable`` flag — the closing ``endif`` then dumps the whole
    block into the manual-conversion list.
    """
    translated = translate_expression(directive.expression)
    if translated is None:
        scope.untranslatable = True
        scope.branches.append(_CondBranch(lua_expr=None, boundary=directive))
        return
    lua_expr, refs = translated
    for name in refs:
        value = owning_doc.variables.get(name)
        if value is not None and name not in state.referenced_vars:
            state.referenced_vars[name] = value
    scope.branches.append(_CondBranch(lua_expr=lua_expr, boundary=directive))


@dataclass
class _RawLua(Line):
    """Synthetic line that carries pre-rendered Lua for a nested conditional.

    When a nested ``# hyprlang if … endif`` closes inside an outer scope's
    branch, the rendered Lua text needs to land in the outer branch's body.
    Wrapping it in a ``Line`` subclass keeps the outer branch's ``lines``
    list homogeneous so the sub-walker can pass over it without special
    casing in every isinstance check.
    """


def _emit_conditional_block(
    scope: _CondScope, outer_state: _EmitState, owning_doc: Document
) -> str:
    """Render a closed scope as a single ``if … elseif … else … end`` chunk.

    Each branch's buffered lines run through a fresh sub-state, the resulting
    flat Lua statements get indented and wrapped by the branch keyword
    (``if EXPR then`` / ``elseif EXPR then`` / ``else``). Skipped entries
    and referenced variables collected by the sub-state bubble up to
    *outer_state* so the trailing manual-conversion block and the local
    preamble see the full picture.
    """
    parts: list[str] = []
    for i, branch in enumerate(scope.branches):
        sub_state = _EmitState()
        for ln in branch.lines:
            if isinstance(ln, _RawLua):
                sub_state.current.extras.append(ln.raw)
            else:
                _process_line(ln, sub_state, owning_doc)
        outer_state.skipped.extend(sub_state.skipped)
        for name, value in sub_state.referenced_vars.items():
            if name not in outer_state.referenced_vars:
                outer_state.referenced_vars[name] = value
        body = _render_state_flat(sub_state)
        if branch.lua_expr is None:
            parts.append("else\n")
        elif i == 0:
            parts.append(f"if {branch.lua_expr} then\n")
        else:
            parts.append(f"elseif {branch.lua_expr} then\n")
        if body:
            parts.append(_indent_block(body, INDENT))
    parts.append("end")
    return "".join(parts)


def _render_state_flat(state: _EmitState) -> str:
    """Render a sub-state as a flat sequence of Lua statements.

    Drops the group-header convention (``-- header`` lines) that
    :func:`_assemble_lua` uses at the top level — inside a conditional
    branch, topical comments would create noise without the visual section
    breaks that make sense at the file scope. ``skipped`` is intentionally
    not rendered here; the caller already bubbles it up to the outer state.
    """
    parts: list[str] = []
    for group in state.groups:
        if group.config_tree:
            parts.append(f"hl.config({format_table(group.config_tree, indent=0)})\n")
        if group.extras:
            parts.extend(f"{call}\n" for call in group.extras)
        if group.exec_once:
            parts.append(format_exec_block("hyprland.start", group.exec_once))
        if group.exec_shutdown:
            parts.append(format_exec_block("hyprland.shutdown", group.exec_shutdown))
    return "".join(parts)


def _indent_block(text: str, prefix: str) -> str:
    """Prefix every non-empty line of *text* with *prefix*.

    Empty lines stay empty (no trailing whitespace on blank rows) so the
    rendered Lua reads cleanly when the branch body has internal spacing.
    """
    return "".join((prefix + ln if ln.strip() else ln) for ln in text.splitlines(keepends=True))
