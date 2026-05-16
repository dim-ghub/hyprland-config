"""Tests for the Hyprlang → Lua converter."""

import pytest

from hyprland_config import analyze_conversion, execute_conversion


@pytest.fixture
def simple_conf(tmp_path):
    """A minimal Hyprland config with no sourced files."""
    conf = tmp_path / "hyprland.conf"
    conf.write_text("general:gaps_in = 5\nbind = SUPER, Q, killactive,\n")
    return conf


@pytest.fixture
def sourced_conf(tmp_path):
    """A Hyprland config that follows two sourced sub-files."""
    (tmp_path / "binds.conf").write_text("bind = SUPER, Q, killactive,\n")
    (tmp_path / "env.conf").write_text("env = XCURSOR_SIZE, 24\n")
    main = tmp_path / "hyprland.conf"
    main.write_text(
        "source = ./binds.conf\nsource = ./env.conf\ngeneral:gaps_in = 5\n",
    )
    return main


class TestAnalyze:
    def test_single_file_plan_writes_one_lua(self, simple_conf) -> None:
        plan = analyze_conversion(simple_conf)
        assert plan.input_path == simple_conf
        assert plan.primary_output == simple_conf.with_suffix(".lua")
        assert len(plan.output_files) == 1
        assert plan.sourced_count == 0

    def test_sourced_files_produce_one_lua_each(self, sourced_conf) -> None:
        plan = analyze_conversion(sourced_conf)
        assert plan.sourced_count == 2
        assert len(plan.output_files) == 3  # parent + 2 children

    def test_plan_includes_primary_output_path(self, sourced_conf) -> None:
        plan = analyze_conversion(sourced_conf)
        assert plan.primary_output in plan.output_files

    def test_no_conflicts_when_lua_does_not_exist(self, simple_conf) -> None:
        plan = analyze_conversion(simple_conf)
        assert plan.existing_lua == []
        assert plan.has_conflicts is False

    def test_detects_existing_lua_conflicts(self, simple_conf) -> None:
        target = simple_conf.with_suffix(".lua")
        target.write_text("-- user already has a lua file\n")
        plan = analyze_conversion(simple_conf)
        assert target in plan.existing_lua
        assert plan.has_conflicts is True

    def test_lists_unmapped_lines_with_source(self, tmp_path) -> None:
        # workspaceopt has no Lua API equivalent; it should surface here.
        conf = tmp_path / "hyprland.conf"
        conf.write_text("bind = SUPER, V, workspaceopt, allfloat\n")
        plan = analyze_conversion(conf)
        assert len(plan.unmapped) == 1
        assert "workspaceopt" in plan.unmapped[0].line
        # ``source`` is the originating .conf, not the would-be .lua output —
        # UIs show it next to the line so the user knows where to port the
        # rule from.
        assert plan.unmapped[0].source == conf

    def test_unmapped_lines_only_count_real_todos(self, simple_conf) -> None:
        # exec-once carries an inline marker annotation — informational,
        # not an unmapped fall-through. The converter's "won't migrate"
        # list must not pick it up.
        simple_conf.write_text("exec-once = waybar\n")
        plan = analyze_conversion(simple_conf)
        assert plan.unmapped == []


class TestExecute:
    def test_writes_each_planned_file(self, simple_conf) -> None:
        plan = analyze_conversion(simple_conf)
        result = execute_conversion(plan)
        assert result.ok
        for path in plan.output_files:
            assert path.exists()
            assert path in result.written

    def test_skips_existing_lua_without_overwrite(self, simple_conf) -> None:
        target = simple_conf.with_suffix(".lua")
        target.write_text("-- user content\n")
        plan = analyze_conversion(simple_conf)

        result = execute_conversion(plan)

        assert target in result.skipped
        assert target not in result.written
        # The user's content survives — never overwritten by default.
        assert target.read_text() == "-- user content\n"

    def test_overwrite_replaces_existing_lua(self, simple_conf) -> None:
        target = simple_conf.with_suffix(".lua")
        target.write_text("-- user content\n")
        plan = analyze_conversion(simple_conf)

        result = execute_conversion(plan, overwrite=True)

        assert target in result.written
        assert target not in result.skipped
        # User content was replaced with the converted output.
        content = target.read_text()
        assert "-- user content" not in content
        assert "hl.config" in content

    def test_input_conf_is_never_modified(self, sourced_conf) -> None:
        original_main = sourced_conf.read_text()
        child = sourced_conf.parent / "binds.conf"
        original_child = child.read_text()

        plan = analyze_conversion(sourced_conf)
        execute_conversion(plan)

        assert sourced_conf.read_text() == original_main
        assert child.read_text() == original_child

    def test_emits_a_lua_per_sourced_file(self, sourced_conf) -> None:
        plan = analyze_conversion(sourced_conf)
        execute_conversion(plan)
        assert (sourced_conf.parent / "binds.lua").exists()
        assert (sourced_conf.parent / "env.lua").exists()
        assert (sourced_conf.parent / "hyprland.lua").exists()
