"""Hyprlang → Lua conditional translation.

Covers the ``# hyprlang if … elif … else … endif`` block translation:
expression mapping, variable preamble emission, nested blocks, sections /
exec / bind inside conditionals, ``noerror`` handling, and the
fail-loudly path when an expression can't be translated.
"""

from hyprland_config import parse_string, serialize_lua
from hyprland_config._lua._emit._conditional import translate_expression
from tests._lua_helpers import assert_lua_compiles, requires_lua


class TestExpressionTranslator:
    """Unit-level coverage of the expression-translator module."""

    def test_string_equality(self):
        result = translate_expression("$GPU == nvidia")
        assert result is not None
        lua, refs = result
        # The LHS gets ``tostring(...)``-wrapped so the comparison works whether
        # the preamble emitted ``var_GPU`` as a string, number, or bool.
        assert lua == 'tostring(var_GPU) == "nvidia"'
        assert refs == {"GPU"}

    def test_string_equality_quoted_rhs(self):
        result = translate_expression('$GPU == "nvidia"')
        assert result is not None
        lua, _ = result
        # Already-quoted RHS unwraps so the output isn't double-quoted.
        assert lua == 'tostring(var_GPU) == "nvidia"'

    def test_string_inequality(self):
        result = translate_expression("$GPU != nvidia")
        assert result is not None
        lua, _ = result
        # `!=` maps to Lua's `~=` operator.
        assert lua == 'tostring(var_GPU) ~= "nvidia"'

    def test_numeric_greater_than(self):
        result = translate_expression("$N > 5")
        assert result is not None
        lua, refs = result
        # Numeric ops wrap LHS in tonumber() because Hyprlang variables are
        # always strings — `"3" > 5` is a Lua type error otherwise.
        assert lua == "tonumber(var_N) > 5"
        assert refs == {"N"}

    def test_numeric_less_than_equal(self):
        result = translate_expression("$N <= 10")
        assert result is not None
        lua, _ = result
        assert lua == "tonumber(var_N) <= 10"

    def test_numeric_float_rhs(self):
        result = translate_expression("$RATIO >= 1.5")
        assert result is not None
        lua, _ = result
        assert lua == "tonumber(var_RATIO) >= 1.5"

    def test_numeric_negative_rhs(self):
        result = translate_expression("$OFFSET > -100")
        assert result is not None
        lua, _ = result
        assert lua == "tonumber(var_OFFSET) > -100"

    def test_bare_var_truthy_check(self):
        result = translate_expression("$GAPS")
        assert result is not None
        lua, refs = result
        # Hyprlang treats a bare variable as truthy when non-empty,
        # non-"0", non-"false"; the Lua approximation has to test all
        # three because Lua strings (including "0" and "false") are
        # always truthy. The falsy checks ``tostring(...)``-wrap the local
        # so numeric and bool locals from the typed preamble still trip
        # the right patterns.
        assert "var_GAPS ~= nil" in lua
        assert 'tostring(var_GAPS) ~= ""' in lua
        assert 'tostring(var_GAPS) ~= "0"' in lua
        assert 'tostring(var_GAPS) ~= "false"' in lua
        assert refs == {"GAPS"}

    def test_numeric_op_with_non_numeric_rhs_fails(self):
        # `>` requires a number; `nvidia` isn't one — bail rather than guess.
        assert translate_expression("$X > nvidia") is None

    def test_compound_expression_fails(self):
        assert translate_expression("$X > 0 and $Y > 0") is None
        assert translate_expression("$X == nvidia or $X == amd") is None

    def test_negation_fails(self):
        assert translate_expression("not $X") is None

    def test_empty_expression_fails(self):
        assert translate_expression("") is None
        assert translate_expression("   ") is None

    def test_bare_literal_fails(self):
        # A bare word with no `$` isn't a recognized shape.
        assert translate_expression("nvidia") is None

    def test_rhs_with_special_chars_quoted(self):
        result = translate_expression('$PATH == "/home/user"')
        assert result is not None
        lua, _ = result
        assert lua == 'tostring(var_PATH) == "/home/user"'


class TestBasicConditional:
    """End-to-end translation of simple if/else/endif blocks."""

    def test_simple_if_endif(self):
        text = (
            "$GPU = nvidia\n# hyprlang if $GPU == nvidia\nenv = MY_GPU, nvidia\n# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'local var_GPU = "nvidia"' in out
        assert 'if tostring(var_GPU) == "nvidia" then' in out
        assert 'hl.env("MY_GPU", "nvidia")' in out
        assert "end" in out

    def test_if_else_endif(self):
        text = (
            "$GPU = nvidia\n"
            "# hyprlang if $GPU == nvidia\n"
            "env = MY_GPU, nvidia\n"
            "# hyprlang else\n"
            "env = MY_GPU, amd\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'if tostring(var_GPU) == "nvidia" then' in out
        assert 'hl.env("MY_GPU", "nvidia")' in out
        assert "else" in out
        assert 'hl.env("MY_GPU", "amd")' in out
        assert out.rstrip().endswith("end")

    def test_elif_chain(self):
        text = (
            "$N = 2\n"
            "# hyprlang if $N == 1\n"
            "env = COUNT, one\n"
            "# hyprlang elif $N == 2\n"
            "env = COUNT, two\n"
            "# hyprlang elif $N == 3\n"
            "env = COUNT, three\n"
            "# hyprlang else\n"
            "env = COUNT, many\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        # ``$N = 2`` coerces to the Lua number 2 — ``tostring`` lets the
        # string-equality compare succeed against the literal ``"1"`` etc.
        assert "local var_N = 2" in out
        assert 'if tostring(var_N) == "1" then' in out
        assert 'elseif tostring(var_N) == "2" then' in out
        assert 'elseif tostring(var_N) == "3" then' in out
        assert "else" in out
        # Every branch's body emits its own hl.env call (none merged).
        assert out.count("hl.env(") == 4

    def test_only_referenced_vars_in_preamble(self):
        # $UNUSED never appears in a conditional or assignment → no
        # ``local var_UNUSED = …``.
        text = (
            "$GPU = nvidia\n"
            "$UNUSED = whatever\n"
            "# hyprlang if $GPU == nvidia\n"
            "env = X, y\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'local var_GPU = "nvidia"' in out
        assert "UNUSED" not in out

    def test_no_preamble_when_no_var_references(self):
        # A variable that's defined but never referenced anywhere stays out
        # of the preamble — matches Hyprlang, which silently ignores unused
        # variables. (Previously this test asserted no preamble when no
        # conditionals; now assignments and binds also pull variables in.)
        out = serialize_lua(parse_string("$M = SUPER\nbind = SUPER, A, exec, foo\n"))
        assert "local " not in out

    def test_each_branch_emits_own_hl_config(self):
        # Branches must NOT merge their assignments into a single hl.config —
        # that would lose the conditional behavior at Lua load time.
        text = (
            "$N = 1\n"
            "# hyprlang if $N == 1\n"
            "general:gaps_in = 5\n"
            "# hyprlang else\n"
            "general:gaps_in = 20\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert out.count("hl.config(") == 2


class TestNestedConditionals:
    """Nested if-blocks recurse through the sub-walker cleanly."""

    def test_two_level_nesting(self):
        text = (
            "$X = 1\n"
            "$Y = 1\n"
            "# hyprlang if $X > 0\n"
            "# hyprlang if $Y > 0\n"
            "env = BOTH, on\n"
            "# hyprlang endif\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert "if tonumber(var_X) > 0 then" in out
        assert "if tonumber(var_Y) > 0 then" in out
        assert 'hl.env("BOTH", "on")' in out
        # Two `end` tokens, one per nested `if` — match on whole-word boundary
        # so the inner `end` (indented) and outer `end` (column 0) both count.
        import re as _re

        assert len(_re.findall(r"\bend\b", out)) == 2

    def test_nested_inside_elif(self):
        text = (
            "$A = 2\n"
            "$B = 1\n"
            "# hyprlang if $A == 1\n"
            "env = A_ONE, yes\n"
            "# hyprlang elif $A == 2\n"
            "# hyprlang if $B > 0\n"
            "env = NESTED, yes\n"
            "# hyprlang endif\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'if tostring(var_A) == "1" then' in out
        assert 'elseif tostring(var_A) == "2" then' in out
        assert "if tonumber(var_B) > 0 then" in out
        assert 'hl.env("NESTED", "yes")' in out

    def test_untranslatable_nested_does_not_block_outer(self):
        # An inner conditional we can't translate surfaces in the TODO block,
        # but the outer translatable block still emits — partial success is
        # better than dropping everything.
        text = (
            "$A = 1\n"
            "$B = 2\n"
            "# hyprlang if $A == 1\n"
            "# hyprlang if $A > 0 and $B > 0\n"
            "env = NESTED, on\n"
            "# hyprlang endif\n"
            "env = OUTER, on\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'if tostring(var_A) == "1" then' in out
        assert 'hl.env("OUTER", "on")' in out
        assert "TODO" in out
        assert "$A > 0 and $B > 0" in out


class TestConditionalWithMixedContent:
    """Conditionals around different body shapes (sections, exec, bind)."""

    def test_section_assignment_inside_conditional(self):
        text = (
            "$GAPS = 1\n"
            "general {\n"
            "    # hyprlang if $GAPS\n"
            "    gaps_in = 5\n"
            "    # hyprlang endif\n"
            "}\n"
        )
        out = serialize_lua(parse_string(text))
        assert "general =" in out
        assert "gaps_in = 5," in out
        # The conditional wrapper survives — body isn't unconditionally emitted.
        assert "var_GAPS ~=" in out
        assert "end" in out

    def test_bind_inside_conditional(self):
        text = (
            "$MOD = SUPER\n"
            "# hyprlang if $MOD == SUPER\n"
            "bind = SUPER, A, exec, alacritty\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'if tostring(var_MOD) == "SUPER" then' in out
        assert "hl.bind(" in out

    def test_exec_inside_conditional_wraps_hl_on(self):
        text = "$DESKTOP = 1\n# hyprlang if $DESKTOP == 1\nexec-once = waybar\n# hyprlang endif\n"
        out = serialize_lua(parse_string(text))
        assert 'if tostring(var_DESKTOP) == "1" then' in out
        # The hl.on block ends up inside the conditional so the start-up
        # handler only registers when the condition fires.
        assert 'hl.on("hyprland.start"' in out
        assert "waybar" in out


class TestNoErrorDirective:
    """The `# hyprlang noerror` directive has no Lua equivalent."""

    def test_noerror_emits_explanatory_comment(self):
        out = serialize_lua(parse_string("# hyprlang noerror true\nenv = X, y\n"))
        assert "noerror has no Lua equivalent" in out
        assert 'hl.env("X", "y")' in out

    def test_noerror_does_not_open_conditional_scope(self):
        # Body lines after `noerror` keep emitting normally — `noerror`
        # isn't an if/endif pair, it's a one-shot directive.
        out = serialize_lua(
            parse_string("# hyprlang noerror true\nenv = X, y\n# hyprlang noerror false\n")
        )
        # Two noerror comments, one env call in the middle, no extra wrapping.
        assert out.count("noerror has no Lua equivalent") == 2
        assert "if " not in out


class TestUntranslatableFallback:
    """When an expression can't be translated, surface the whole block."""

    def test_compound_expression_in_todo_with_body(self):
        text = (
            "$X = 1\n"
            "$Y = 1\n"
            "# hyprlang if $X > 0 and $Y > 0\n"
            "env = BOTH, on\n"
            "# hyprlang else\n"
            "env = ONE, off\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        # No partial translation lands in the live output.
        assert "if " not in out or "TODO" in out
        # Every line of the original block is preserved in the TODO list so
        # the user can port it by hand.
        assert "$X > 0 and $Y > 0" in out
        assert "env = BOTH, on" in out
        assert "env = ONE, off" in out
        assert "# hyprlang endif" in out

    def test_partial_branches_translatable_still_bails(self):
        # One branch fails → whole block goes to TODO. We don't half-translate
        # a conditional because runtime semantics depend on all branches
        # being consistent (an untranslated elif means the else fires for
        # cases the elif would have caught).
        text = (
            "$X = 1\n"
            "$Y = 1\n"
            "# hyprlang if $X == 1\n"
            "env = OK, a\n"
            "# hyprlang elif $X > 0 and $Y > 0\n"
            "env = BAD, b\n"
            "# hyprlang endif\n"
        )
        out = serialize_lua(parse_string(text))
        assert 'hl.env("OK"' not in out
        assert "TODO" in out

    def test_orphan_endif_surfaces_in_todo(self):
        out = serialize_lua(parse_string("env = X, y\n# hyprlang endif\n"))
        assert 'hl.env("X", "y")' in out
        assert "# hyprlang endif" in out
        assert "TODO" in out

    def test_missing_endif_surfaces_in_todo(self):
        # An ``if`` with no matching ``endif`` (truncated file, in-progress
        # edit) buffers the body forever otherwise — drain the open scope
        # at assembly time so the user sees what got dropped.
        text = "$X = 1\n# hyprlang if $X == 1\nenv = STUCK, yes\n"
        out = serialize_lua(parse_string(text))
        assert "# hyprlang if $X == 1" in out
        assert "env = STUCK, yes" in out
        assert "TODO" in out
        # No partial `if EXPR then` block should leak into the live output —
        # without the endif we can't safely close the Lua block either.
        assert "if X == " not in out


class TestLuaValidity:
    """Translated output parses cleanly through `luac -p`."""

    @requires_lua
    def test_simple_if_compiles(self):
        text = (
            "$GPU = nvidia\n# hyprlang if $GPU == nvidia\nenv = MY_GPU, nvidia\n# hyprlang endif\n"
        )
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_elif_chain_compiles(self):
        text = (
            "$N = 2\n"
            "# hyprlang if $N == 1\n"
            "env = COUNT, one\n"
            "# hyprlang elif $N == 2\n"
            "env = COUNT, two\n"
            "# hyprlang else\n"
            "env = COUNT, many\n"
            "# hyprlang endif\n"
        )
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_nested_compiles(self):
        text = (
            "$X = 1\n"
            "$Y = 1\n"
            "# hyprlang if $X > 0\n"
            "# hyprlang if $Y > 0\n"
            "env = BOTH, on\n"
            "# hyprlang endif\n"
            "# hyprlang endif\n"
        )
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_bare_var_truthy_compiles(self):
        text = "$GAPS = 1\n# hyprlang if $GAPS\ngeneral:gaps_in = 5\n# hyprlang endif\n"
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_section_with_conditional_compiles(self):
        text = (
            "$GAPS = 1\n"
            "general {\n"
            "    # hyprlang if $GAPS\n"
            "    gaps_in = 5\n"
            "    # hyprlang endif\n"
            "}\n"
        )
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_exec_inside_conditional_compiles(self):
        text = "$DESKTOP = 1\n# hyprlang if $DESKTOP == 1\nexec-once = waybar\n# hyprlang endif\n"
        assert_lua_compiles(serialize_lua(parse_string(text)))

    @requires_lua
    def test_bind_inside_conditional_compiles(self):
        text = (
            "$MOD = SUPER\n"
            "# hyprlang if $MOD == SUPER\n"
            "bind = SUPER, A, exec, alacritty\n"
            "# hyprlang endif\n"
        )
        assert_lua_compiles(serialize_lua(parse_string(text)))
