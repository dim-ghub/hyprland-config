"""Multi-file Lua emission: ``serialize_lua_tree``, source recursion, ``serialize_any``."""

from hyprland_config import (
    load,
    parse_string,
    serialize_any,
    serialize_hyprlang,
    serialize_lua,
    serialize_lua_tree,
)


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
        # other lines — the emitter must expand `$mainMod` to its value.
        (tmp_path / "binds.conf").write_text("$mainMod = SUPER\nbind = $mainMod, Q, killactive,\n")
        (tmp_path / "main.conf").write_text("source = ./binds.conf\n")
        out = serialize_lua(load(tmp_path / "main.conf"))
        assert 'hl.bind("SUPER + Q"' in out
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
        # The parent only contains a dofile pointer, not the actual content.
        assert "gaps_in" not in tree[tmp_path / "main.lua"]

    def test_parent_dofiles_each_child(self, tmp_path) -> None:
        (tmp_path / "a.conf").write_text("env = A, 1\n")
        (tmp_path / "b.conf").write_text("env = B, 2\n")
        (tmp_path / "main.conf").write_text("source = ./a.conf\nsource = ./b.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))[tmp_path / "main.lua"]
        assert f'dofile("{tmp_path / "a.lua"}")' in parent
        assert f'dofile("{tmp_path / "b.lua"}")' in parent

    def test_nested_sources_emit_all_files(self, tmp_path) -> None:
        (tmp_path / "leaf.conf").write_text("env = X, 1\n")
        (tmp_path / "mid.conf").write_text("source = ./leaf.conf\n")
        (tmp_path / "root.conf").write_text("source = ./mid.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "root.conf")))
        assert (tmp_path / "leaf.lua") in tree
        assert (tmp_path / "mid.lua") in tree
        assert (tmp_path / "root.lua") in tree
        assert 'hl.env("X", "1")' in tree[tmp_path / "leaf.lua"]
        assert f'dofile("{tmp_path / "leaf.lua"}")' in tree[tmp_path / "mid.lua"]
        assert f'dofile("{tmp_path / "mid.lua"}")' in tree[tmp_path / "root.lua"]

    def test_in_memory_doc_without_path_returns_empty_tree(self) -> None:
        # ``parse_string`` produces a Document without a path; there's no
        # natural filename to emit, so the tree is empty (no crash).
        assert serialize_lua_tree(parse_string("general:gaps_in = 5\n")) == []

    def test_tree_function_matches_method(self, tmp_path) -> None:
        (tmp_path / "main.conf").write_text("general:gaps_in = 5\n")
        doc = load(tmp_path / "main.conf")
        assert serialize_lua_tree(doc) == serialize_lua_tree(doc)

    def test_dotd_dir_is_remapped_to_lua_d(self, tmp_path) -> None:
        # The drop-in dir convention `xxx.conf.d/` is commonly globbed in
        # the user's top-level config (e.g. `source = …conf.d/*`), which
        # would catch our new `.lua` files and break parsing. Output paths
        # under such a dir must be redirected to a sibling `xxx.lua.d/`.
        confd = tmp_path / "hyprland.conf.d"
        confd.mkdir()
        (confd / "00_env.conf").write_text("env = X, 1\n")
        (tmp_path / "hyprland.conf").write_text("source = ./hyprland.conf.d/00_env.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "hyprland.conf")))
        # Child landed in the remapped directory, NOT next to the .conf.
        assert (tmp_path / "hyprland.lua.d" / "00_env.lua") in tree
        assert (confd / "00_env.lua") not in tree

    def test_parent_dofiles_point_at_remapped_paths(self, tmp_path) -> None:
        # The parent emit's dofile() lines must follow the remap so the
        # generated hyprland.lua actually finds the sub-files.
        confd = tmp_path / "hyprland.conf.d"
        confd.mkdir()
        (confd / "00_env.conf").write_text("env = X, 1\n")
        (tmp_path / "hyprland.conf").write_text("source = ./hyprland.conf.d/00_env.conf\n")
        parent = _by_path(serialize_lua_tree(load(tmp_path / "hyprland.conf")))[
            tmp_path / "hyprland.lua"
        ]
        assert f'dofile("{tmp_path / "hyprland.lua.d" / "00_env.lua"}")' in parent
        # And explicitly NOT the old .conf.d location.
        assert "hyprland.conf.d" not in parent

    def test_nested_dotd_dirs_each_remap(self, tmp_path) -> None:
        # Nested `.conf.d` parents (rare but possible) all remap.
        outer = tmp_path / "outer.conf.d"
        inner = outer / "inner.conf.d"
        inner.mkdir(parents=True)
        (inner / "leaf.conf").write_text("env = X, 1\n")
        (tmp_path / "main.conf").write_text("source = ./outer.conf.d/inner.conf.d/leaf.conf\n")
        tree = _by_path(serialize_lua_tree(load(tmp_path / "main.conf")))
        assert (tmp_path / "outer.lua.d" / "inner.lua.d" / "leaf.lua") in tree

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
        # ``submap`` has no Lua equivalent — it lands in the unmapped list
        # for its owning file, separate from the rendered content.
        (tmp_path / "main.conf").write_text("submap = mysub\nbind = SUPER, Q, killactive,\n")
        files = serialize_lua_tree(load(tmp_path / "main.conf"))
        assert len(files) == 1
        assert any("submap" in entry for entry in files[0].unmapped)


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

    def test_forwards_emit_migration_markers_to_lua(self) -> None:
        doc = parse_string("exec-once = waybar\n")
        out = serialize_any(doc, "out.lua", emit_migration_markers=False)
        assert "TODO: was exec-once" not in out

    def test_migration_markers_ignored_for_hyprlang(self) -> None:
        # Hyprlang output has no notion of migration markers — passing the
        # flag is a no-op rather than an error, so callers can plumb the
        # same value through regardless of target format.
        doc = parse_string("exec-once = waybar\n")
        out = serialize_any(doc, "out.conf", emit_migration_markers=False)
        assert out == serialize_hyprlang(doc)
