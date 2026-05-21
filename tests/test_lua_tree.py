"""Multi-file Lua emission: ``serialize_lua_tree``, source recursion, ``serialize_any``."""

from hyprland_config import (
    load,
    parse_string,
    serialize_any,
    serialize_hyprlang,
    serialize_lua,
)
from hyprland_config._lua import serialize_lua_tree


class TestSourceRecursion:
    """Sourced files must contribute their content to the emitted Lua.

    Real-world configs split themselves across `hyprland.conf.d/*.conf` or
    similar; an emitter that only sees the top-level file is useless.
    """

    def test_sourced_assignments_emit(self, tmp_path) -> None:
        (tmp_path / "child.conf").write_text("general:gaps_in = 7\n")
        (tmp_path / "parent.conf").write_text("source = ./child.conf\n")
        out = serialize_lua(load(tmp_path / "parent.conf"))
        assert "gaps_in = 7" in out

    def test_sourced_keywords_emit(self, tmp_path) -> None:
        (tmp_path / "binds.conf").write_text("bind = SUPER, Q, killactive,\n")
        (tmp_path / "main.conf").write_text("source = ./binds.conf\n")
        out = serialize_lua(load(tmp_path / "main.conf"))
        assert "hl.bind(" in out

    def test_sourced_variables_expand_in_sibling_lines(self, tmp_path) -> None:
        # Variables declared in the same sourced file are visible to its
        # other lines — the emitter must register them in the preamble and
        # reference the Lua local at the bind call site.
        (tmp_path / "binds.conf").write_text("$mainMod = SUPER\nbind = $mainMod, Q, killactive,\n")
        (tmp_path / "main.conf").write_text("source = ./binds.conf\n")
        out = serialize_lua(load(tmp_path / "main.conf"))
        assert 'local var_mainMod = "SUPER"' in out
        assert 'hl.bind(var_mainMod .. " + Q"' in out
        assert "$mainMod" not in out


def _by_path(tree) -> dict:
    """Test helper: ``LuaFile`` list → ``{path: content}`` dict for lookup."""
    return {f.path: f.content for f in tree}


class TestSerializeLuaTree:
    """Multi-file emit preserves the user's ``source = …`` structure."""

    def test_single_file_doc_emits_single_lua(self, tmp_path) -> None:
        (tmp_path / "only.conf").write_text("general:gaps_in = 5\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "only.conf")))
        assert set(tree) == {tmp_path / "only.lua"}
        assert "gaps_in = 5" in tree[tmp_path / "only.lua"]

    def test_one_lua_file_per_sourced_doc(self, tmp_path) -> None:
        (tmp_path / "general.conf").write_text("general:gaps_in = 5\n")
        (tmp_path / "binds.conf").write_text("bind = SUPER, Q, killactive,\n")
        (tmp_path / "main.conf").write_text("source = ./general.conf\nsource = ./binds.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        assert (tmp_path / "main.lua") in tree
        assert (tmp_path / "general.lua") in tree
        assert (tmp_path / "binds.lua") in tree

    def test_sourced_content_lives_in_child_file_not_parent(self, tmp_path) -> None:
        (tmp_path / "general.conf").write_text("general:gaps_in = 7\n")
        (tmp_path / "main.conf").write_text("source = ./general.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        assert "gaps_in = 7" in tree[tmp_path / "general.lua"]
        # The parent only contains a require() pointer, not the actual content.
        assert "gaps_in" not in tree[tmp_path / "main.lua"]

    def test_parent_requires_each_child(self, tmp_path) -> None:
        # Siblings of the entry file become bare module names — the form
        # Hyprland's autoreload watches, not an absolute dofile().
        (tmp_path / "a.conf").write_text("env = A, 1\n")
        (tmp_path / "b.conf").write_text("env = B, 2\n")
        (tmp_path / "main.conf").write_text("source = ./a.conf\nsource = ./b.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))[tmp_path / "main.lua"]
        assert 'require("a")' in parent
        assert 'require("b")' in parent
        assert "dofile" not in parent

    def test_subdir_source_uses_dotted_require(self, tmp_path) -> None:
        # A file in a subdirectory maps to dot-notation: package.path turns
        # the dots back into "/" so require("modules.monitors") finds
        # <root>/modules/monitors.lua.
        (tmp_path / "modules").mkdir()
        (tmp_path / "modules" / "monitors.conf").write_text("monitor = DP-1, preferred, auto, 1\n")
        (tmp_path / "main.conf").write_text("source = ./modules/monitors.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))[tmp_path / "main.lua"]
        assert 'require("modules.monitors")' in parent

    def test_nested_sources_emit_all_files(self, tmp_path) -> None:
        (tmp_path / "leaf.conf").write_text("env = X, 1\n")
        (tmp_path / "mid.conf").write_text("source = ./leaf.conf\n")
        (tmp_path / "root.conf").write_text("source = ./mid.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "root.conf")))
        assert (tmp_path / "leaf.lua") in tree
        assert (tmp_path / "mid.lua") in tree
        assert (tmp_path / "root.lua") in tree
        assert 'hl.env("X", "1")' in tree[tmp_path / "leaf.lua"]
        # Module names are resolved against the one config root (root.conf's
        # dir), so nested includes are bare names just like the top-level one.
        assert 'require("leaf")' in tree[tmp_path / "mid.lua"]
        assert 'require("mid")' in tree[tmp_path / "root.lua"]

    def test_in_memory_doc_without_path_returns_empty_tree(self) -> None:
        # ``parse_string`` produces a Document without a path; there's no
        # natural filename to emit, so the tree is empty (no crash).
        assert serialize_lua_tree(parse_string("general:gaps_in = 5\n")) == []

    def test_tree_function_matches_method(self, tmp_path) -> None:
        (tmp_path / "main.conf").write_text("general:gaps_in = 5\n")
        doc = load(tmp_path / "main.conf")
        assert serialize_lua_tree(doc) == serialize_lua_tree(doc)

    def test_dotd_dir_is_flattened_to_plain_subdir(self, tmp_path) -> None:
        # The drop-in dir convention `xxx.conf.d/` is commonly globbed in
        # the user's top-level config (e.g. `source = …conf.d/*`), which
        # would catch our new `.lua` files and break parsing. We flatten the
        # `.conf.d` suffix to a plain `xxx/` — a different name (so the old
        # glob misses it) that's also dot-free (so require() can name it).
        confd = tmp_path / "hyprland.conf.d"
        confd.mkdir()
        (confd / "00_env.conf").write_text("env = X, 1\n")
        (tmp_path / "hyprland.conf").write_text("source = ./hyprland.conf.d/00_env.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "hyprland.conf")))
        # Child landed in the flattened directory, NOT next to the .conf.
        assert (tmp_path / "hyprland" / "00_env.lua") in tree
        assert (confd / "00_env.lua") not in tree

    def test_parent_requires_flattened_dropins(self, tmp_path) -> None:
        # Once the drop-in dir is dot-free, its files are reachable by
        # require() — so they reload on save like every other sub-file,
        # and the parent points at the flattened location, not `.conf.d`.
        confd = tmp_path / "hyprland.conf.d"
        confd.mkdir()
        (confd / "00_env.conf").write_text("env = X, 1\n")
        (tmp_path / "hyprland.conf").write_text("source = ./hyprland.conf.d/00_env.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "hyprland.conf")))[
            tmp_path / "hyprland.lua"
        ]
        assert 'require("hyprland.00_env")' in parent
        assert "dofile" not in parent
        # And explicitly NOT the old .conf.d location.
        assert "hyprland.conf.d" not in parent

    def test_source_outside_config_dir_falls_back_to_dofile(self, tmp_path) -> None:
        # A file outside the main config's directory isn't on package.path,
        # so require() can't reach it — keep an absolute dofile().
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        other = tmp_path / "shared"
        other.mkdir()
        (other / "x.conf").write_text("env = X, 1\n")
        (cfg / "hyprland.conf").write_text(f"source = {other / 'x.conf'}\n")
        parent = _by_path(serialize_lua_tree(load(cfg / "hyprland.conf")))[cfg / "hyprland.lua"]
        assert f'dofile("{other / "x.lua"}")' in parent
        assert "require(" not in parent

    def test_dotted_filename_stem_falls_back_to_dofile(self, tmp_path) -> None:
        # A dot in the file stem (colors.dark.lua) would split into two module
        # segments under package.path, so this also stays on dofile().
        (tmp_path / "colors.dark.conf").write_text("env = X, 1\n")
        (tmp_path / "main.conf").write_text("source = ./colors.dark.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))[tmp_path / "main.lua"]
        assert f'dofile("{tmp_path / "colors.dark.lua"}")' in parent
        assert "require(" not in parent

    def test_nested_dotd_dirs_each_flatten(self, tmp_path) -> None:
        # Nested `.conf.d` parents (rare but possible) each lose the suffix,
        # leaving a fully dot-free path that require() can name end to end.
        outer = tmp_path / "outer.conf.d"
        inner = outer / "inner.conf.d"
        inner.mkdir(parents=True)
        (inner / "leaf.conf").write_text("env = X, 1\n")
        (tmp_path / "main.conf").write_text("source = ./outer.conf.d/inner.conf.d/leaf.conf\n")
        tree = serialize_lua_tree(load(tmp_path / "main.conf"))
        assert (tmp_path / "outer" / "inner" / "leaf.lua") in _by_path(tree)
        parent = _by_path(tree)[tmp_path / "main.lua"]
        assert 'require("outer.inner.leaf")' in parent

    def test_regular_subdir_not_remapped(self, tmp_path) -> None:
        # Only `.conf.d` triggers the remap — a normal subdir (e.g. `gui/`)
        # keeps its name so users with their own layout aren't surprised.
        subdir = tmp_path / "gui"
        subdir.mkdir()
        (subdir / "extra.conf").write_text("env = X, 1\n")
        (tmp_path / "main.conf").write_text("source = ./gui/extra.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        assert (subdir / "extra.lua") in tree

    def test_unmapped_lines_recorded_per_file(self, tmp_path) -> None:
        # An untranslatable line (here a malformed ``plugin =`` with no
        # path) lands in the unmapped list for its owning file, separate
        # from the rendered content.
        (tmp_path / "main.conf").write_text("plugin =\nbind = SUPER, Q, killactive,\n")
        files = serialize_lua_tree(load(tmp_path / "main.conf"))
        assert len(files) == 1
        assert any("plugin" in entry for entry in files[0].unmapped)


class TestCrossFileVariables:
    """A ``$var`` used in a file other than the one defining it can't ride a
    file-local ``local`` — Lua chunks don't share locals across ``require`` —
    so it becomes a global on the shared ``_G``. Variables used only within
    their defining file stay ``local``. This is the multi-file counterpart to
    the single-chunk ``local`` behaviour in ``test_lua_emit.py``; before it,
    cross-file references leaked through as the literal string ``"$var"`` that
    Hyprland rejected at reload.
    """

    def test_cross_file_var_exported_as_global(self, tmp_path) -> None:
        (tmp_path / "vars.conf").write_text("$terminal = ghostty\n")
        (tmp_path / "binds.conf").write_text("bind = SUPER, Return, exec, $terminal\n")
        (tmp_path / "main.conf").write_text("source = ./vars.conf\nsource = ./binds.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        # Defining file exports a bare global (no `local`).
        assert 'var_terminal = "ghostty"' in tree[tmp_path / "vars.lua"]
        assert "local var_terminal" not in tree[tmp_path / "vars.lua"]
        # Consumer reads the global, doesn't re-declare it, no literal "$terminal".
        binds = tree[tmp_path / "binds.lua"]
        assert "hl.dsp.exec_cmd(var_terminal)" in binds
        assert "local var_terminal" not in binds
        assert "$terminal" not in binds

    def test_file_local_var_stays_local(self, tmp_path) -> None:
        # $mainMod is defined and used only in binds.conf, so it needs no
        # global; $terminal crosses from vars.conf and does.
        (tmp_path / "vars.conf").write_text("$terminal = ghostty\n")
        (tmp_path / "binds.conf").write_text(
            "$mainMod = SUPER\nbind = $mainMod, Return, exec, $terminal\n"
        )
        (tmp_path / "main.conf").write_text("source = ./vars.conf\nsource = ./binds.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        binds = tree[tmp_path / "binds.lua"]
        assert 'local var_mainMod = "SUPER"' in binds
        assert "var_mainMod" not in tree[tmp_path / "vars.lua"]
        assert 'var_terminal = "ghostty"' in tree[tmp_path / "vars.lua"]
        assert "hl.dsp.exec_cmd(var_terminal)" in binds

    def test_cross_file_chain_promotes_dependency_to_global(self, tmp_path) -> None:
        # $accent = $primary (both in colors.conf); $accent is used in
        # deco.conf, so it's a global — and its dependency $primary must be a
        # global too, declared before it.
        (tmp_path / "colors.conf").write_text("$primary = rgb(98a8b3)\n$accent = $primary\n")
        (tmp_path / "deco.conf").write_text("general:col.active_border = $accent\n")
        (tmp_path / "main.conf").write_text("source = ./colors.conf\nsource = ./deco.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        colors = tree[tmp_path / "colors.lua"]
        assert 'var_primary = "rgb(98a8b3)"' in colors
        assert "var_accent = var_primary" in colors
        assert colors.index("var_primary") < colors.index("var_accent")
        assert "local " not in colors  # both globals, neither a local
        assert "active_border = var_accent," in tree[tmp_path / "deco.lua"]

    def test_unused_var_not_emitted(self, tmp_path) -> None:
        # A variable defined but never referenced stays out of the output,
        # matching single-file behaviour and Hyprlang's silent-ignore.
        (tmp_path / "vars.conf").write_text("$used = ghostty\n$unused = whatever\n")
        (tmp_path / "binds.conf").write_text("bind = SUPER, Return, exec, $used\n")
        (tmp_path / "main.conf").write_text("source = ./vars.conf\nsource = ./binds.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        joined = "\n".join(tree.values())
        assert "var_unused" not in joined
        assert "whatever" not in joined
        assert 'var_used = "ghostty"' in tree[tmp_path / "vars.lua"]

    def test_rule_matcher_var_promoted_across_files(self, tmp_path) -> None:
        # A ``$var`` inside a Rule's matcher (defined in another file) has
        # to be promoted to a global too — the Rule path bypasses the keyword
        # emitter, so the classifier needs to walk matcher/effect values.
        (tmp_path / "vars.conf").write_text("$myclass = kitty\n")
        (tmp_path / "rules.conf").write_text("windowrule = float, match:class $myclass\n")
        (tmp_path / "main.conf").write_text("source = ./vars.conf\nsource = ./rules.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        assert 'var_myclass = "kitty"' in tree[tmp_path / "vars.lua"]
        assert "local var_myclass" not in tree[tmp_path / "vars.lua"]
        rules = tree[tmp_path / "rules.lua"]
        assert "class = var_myclass" in rules
        assert "$myclass" not in rules


class TestSerializeAny:
    """``serialize_any`` dispatches on the path suffix — the write-side
    counterpart to ``load_any``."""

    def test_lua_suffix_routes_to_serialize_lua(self) -> None:
        doc = parse_string("general:gaps_in = 5\n")
        assert serialize_any(doc, "out.lua") == serialize_lua(doc)

    def test_conf_suffix_routes_to_doc_serialize(self) -> None:
        doc = parse_string("general:gaps_in = 5\n")
        assert serialize_any(doc, "out.conf") == serialize_hyprlang(doc)

    def test_no_suffix_treated_as_hyprlang(self) -> None:
        doc = parse_string("general:gaps_in = 5\n")
        assert serialize_any(doc, "out") == serialize_hyprlang(doc)

    def test_accepts_pathlib_path(self, tmp_path) -> None:
        doc = parse_string("general:gaps_in = 5\n")
        assert serialize_any(doc, tmp_path / "x.lua") == serialize_lua(doc)
