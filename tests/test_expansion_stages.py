"""Each expansion stage on its own: replacement lists, arguments, substitution, rescan, output."""

from collections import deque

import pytest

from cpre._arguments import bind_arguments, collect_arguments
from cpre._expansion_output import SourceLocator, SourceMapping, merged_edits, render
from cpre._replacement import (
    VA_OPT_KIND,
    ExpansionError,
    ReplacementCache,
    parse_replacement,
)
from cpre._rescan import Rescanner
from cpre._substitution import Substituter
from cpre.errors import SourceLocation
from cpre.expansion import Expansion
from cpre.lexer import Token, tokenize
from cpre.macros import MacroDefinition, MacroState
from cpre.robdd import AnalysisBudget, AnalysisLimitExceeded


def significant(text: str) -> list[Token]:
    return [t for t in tokenize(text) if t.kind not in {"space", "comment"}]


def texts(tokens: list[Token]) -> list[str]:
    return [t.text for t in tokens]


class Environment:
    """A macro environment over plain definitions."""

    def __init__(self, *definitions: MacroDefinition):
        self.definitions = {d.name: d for d in definitions}

    def get(self, name: str) -> MacroState:
        definition = self.definitions.get(name)
        return MacroState(defined=True, definition=definition) if definition else MacroState()


def macro(name, replacement, parameters=None, variadic=False):
    return MacroDefinition(name, replacement, parameters, variadic)


# Stage 1: replacement lists


def test_replacement_lifts_va_opt_groups_into_placeholders():
    definition = macro("F", "a __VA_OPT__(b ## c) d", ("x",), True)
    replacement = parse_replacement(definition)
    assert [t.kind for t in replacement.body] == ["identifier", VA_OPT_KIND, "identifier"]
    (binding,) = replacement.va_opts
    assert texts(replacement.va_opts[binding]) == ["b", "##", "c"]


def test_replacement_numbers_each_va_opt_group():
    replacement = parse_replacement(macro("F", "__VA_OPT__(a) __VA_OPT__(b)", (), True))
    assert len(replacement.va_opts) == 2
    assert [texts(v) for v in replacement.va_opts.values()] == [["a"], ["b"]]


@pytest.mark.parametrize(
    "definition, message",
    [
        (macro("F", "__VA_OPT__(x)"), "requires a variadic function-like macro"),
        (macro("F", "__VA_OPT__ x", (), True), "must be followed by a parenthesized"),
        (macro("F", "__VA_OPT__(x", (), True), "unterminated __VA_OPT__"),
        (macro("F", "__VA_OPT__(__VA_OPT__(x))", (), True), "nested __VA_OPT__"),
        (macro("F", "__VA_OPT__(## x)", (), True), "edge of __VA_OPT__"),
        (macro("F", "__VA_OPT__(a ## ## b)", (), True), "invalid token paste placement"),
        (macro("F", "__VA_OPT__(# y)", (), True), "not followed by a parameter"),
    ],
)
def test_replacement_rejects_malformed_va_opt(definition, message):
    with pytest.raises(ExpansionError, match=message):
        parse_replacement(definition)


def test_replacement_cache_validates_definition_names_and_parameters():
    cache = ReplacementCache()
    with pytest.raises(ExpansionError, match="cannot be used as a macro name"):
        cache.validate(macro("__VA_OPT__", "1"))
    with pytest.raises(ExpansionError, match="cannot be a named parameter"):
        cache.validate(macro("F", "1", ("__VA_OPT__",)))


def test_replacement_cache_parses_a_definition_once():
    cache = ReplacementCache()
    definition = macro("F", "x", ("x",))
    assert cache.get(definition) is cache.get(macro("G", "x", ("x",)))


# Stage 2: arguments


def test_collect_arguments_splits_on_top_level_commas_only():
    pending = deque(significant("(a, (b, c), d) rest"))
    arguments, separators, closing = collect_arguments(pending, "F", AnalysisBudget(1000))
    assert [texts(a) for a in arguments] == [["a"], ["(", "b", ",", "c", ")"], ["d"]]
    assert texts(separators) == [",", ","]
    assert closing.text == ")"
    assert texts(list(pending)) == ["rest"]


def test_collect_arguments_reports_an_unterminated_invocation():
    with pytest.raises(ExpansionError, match="unterminated invocation of F"):
        collect_arguments(deque(significant("(a, b")), "F", AnalysisBudget(1000))


def test_collect_arguments_spends_budget():
    with pytest.raises(AnalysisLimitExceeded):
        collect_arguments(deque(significant("(a b c d e f)")), "F", AnalysisBudget(3))


def test_bind_arguments_names_arguments_after_parameters():
    bound = bind_arguments(macro("F", "", ("x", "y")), [significant("1"), significant("2")], [])
    assert {name: texts(value) for name, value in bound.items()} == {"x": ["1"], "y": ["2"]}


def test_bind_arguments_folds_the_variadic_tail_with_its_commas():
    arguments = [significant("1"), significant("2"), significant("3")]
    separators = significant(", ,")
    bound = bind_arguments(macro("F", "", ("x",), True), arguments, separators)
    assert texts(bound["x"]) == ["1"]
    assert texts(bound["__VA_ARGS__"]) == ["2", ",", "3"]


def test_bind_arguments_treats_an_empty_list_as_no_arguments():
    assert bind_arguments(macro("F", "", ()), [[]], []) == {}


def test_bind_arguments_checks_the_argument_count():
    with pytest.raises(ExpansionError, match="expects 2 arguments, got 1"):
        bind_arguments(macro("F", "", ("x", "y")), [significant("1")], [])
    with pytest.raises(ExpansionError, match="requires a variadic argument"):
        bind_arguments(macro("F", "", ("x",), True), [significant("1")], [])


# Stage 3: substitution


def substitute(definition, raw=None, expanded=None, source=""):
    replacement = parse_replacement(definition)
    raw = raw or {}
    return Substituter(source).substitute(
        replacement.body,
        definition,
        raw,
        expanded if expanded is not None else raw,
        0,
        10,
        replacement.va_opts,
    )


def test_substitute_replaces_parameters_with_prescanned_arguments():
    out = substitute(
        macro("F", "x + x", ("x",)), {"x": significant("raw")}, {"x": significant("1")}
    )
    assert texts(out) == ["1", "+", "1"]


def test_substitute_uses_raw_arguments_beside_paste():
    definition = macro("F", "x ## y", ("x", "y"))
    out = substitute(
        definition,
        {"x": significant("a"), "y": significant("b")},
        {"x": significant("A"), "y": significant("B")},
    )
    assert texts(out) == ["ab"]
    assert out[0].generated


def test_substitute_stringifies_the_invocation_spelling():
    source = "F(a   /* c */ b)"
    tokens = [t for t in tokenize(source) if t.kind not in {"space", "comment"}]
    out = substitute(
        macro("S", "#x", ("x",)), {"x": tokens[2:4]}, {"x": tokens[2:4]}, source=source
    )
    assert texts(out) == ['"a b"']


def test_substitute_escapes_quotes_and_backslashes_when_stringifying():
    source = r'"a\n"'
    tokens = significant(source)
    out = substitute(macro("S", "#x", ("x",)), {"x": tokens}, {"x": tokens}, source=source)
    assert texts(out) == [r'"\"a\\n\""']


def test_substitute_pastes_around_empty_arguments_with_placemarkers():
    definition = macro("F", "x ## y", ("x", "y"))
    out = substitute(definition, {"x": [], "y": significant("b")})
    assert texts(out) == ["b"]
    out = substitute(definition, {"x": [], "y": []})
    assert [t.kind for t in out] == ["empty"]


def test_substitute_rejects_invalid_paste_and_stringify():
    with pytest.raises(ExpansionError, match="invalid token paste in F"):
        substitute(macro("F", "x ## y", ("x", "y")), {"x": significant("+"), "y": significant("-")})
    with pytest.raises(ExpansionError, match="edge of F"):
        substitute(macro("F", "## x", ("x",)), {"x": significant("1")})
    with pytest.raises(ExpansionError, match="placement in F"):
        substitute(macro("F", "x ## ## x", ("x",)), {"x": significant("1")})
    with pytest.raises(ExpansionError, match="not followed by a parameter"):
        substitute(macro("F", "# y", ("x",)), {"x": significant("1")})
    with pytest.raises(ExpansionError, match="not followed by a parameter"):
        substitute(macro("F", "x #", ("x",)), {"x": significant("1")})


def test_substitute_keeps_hash_literal_in_object_like_macros():
    assert texts(substitute(macro("O", "# x"))) == ["#", "x"]


def test_substitute_expands_va_opt_only_for_a_non_empty_variadic_argument():
    definition = macro("F", "a __VA_OPT__(b __VA_ARGS__) c", (), True)
    present = {"__VA_ARGS__": significant("1")}
    assert texts(substitute(definition, present)) == ["a", "b", "1", "c"]
    absent = {"__VA_ARGS__": []}
    assert texts(substitute(definition, absent)) == ["a", "c"]


# Stage 4: rescan


def expand(text: str, *definitions: MacroDefinition, limit: int = 10_000) -> list[str]:
    budget = AnalysisBudget(limit)
    rescanner = Rescanner(budget, Substituter(text), ReplacementCache())
    out = rescanner.expand(significant(text), Environment(*definitions))
    return texts([t for t in out if t.kind != "empty"])


def test_rescan_expands_object_and_function_like_macros():
    assert expand("A F(2)", macro("A", "1"), macro("F", "x + x", ("x",))) == ["1", "2", "+", "2"]


def test_rescan_leaves_a_function_like_name_without_arguments_alone():
    assert expand("F", macro("F", "1", ())) == ["F"]


def test_rescan_prescans_arguments_but_not_stringified_ones():
    definitions = (macro("A", "1"), macro("S", "#x", ("x",)), macro("I", "x", ("x",)))
    assert expand("I(A)", *definitions) == ["1"]
    assert expand("S(A)", *definitions) == ['"A"']


def test_rescan_paints_a_name_blue_inside_its_own_expansion():
    assert expand("A", macro("A", "A + 1")) == ["A", "+", "1"]
    assert expand("B", macro("B", "C + 1"), macro("C", "B")) == ["B", "+", "1"]


def test_rescan_nests_deeply_without_python_recursion():
    depth = 1200
    text = "I(" * depth + "1" + ")" * depth
    assert expand(text, macro("I", "x", ("x",)), limit=10**8) == ["1"]


def test_rescan_rejects_an_assumed_macro_with_unknown_replacement():
    class Assumed:
        def get(self, name):
            return MacroState(defined=True) if name == "X" else MacroState()

    rescanner = Rescanner(AnalysisBudget(100), Substituter("X"), ReplacementCache())
    with pytest.raises(ExpansionError, match="assumed macro X is unknown"):
        rescanner.expand(significant("X"), Assumed())


def test_rescan_rejects_a_stray_va_opt():
    with pytest.raises(ExpansionError, match="only valid in a variadic"):
        expand("__VA_OPT__")


def test_rescan_records_the_offset_it_reached():
    budget = AnalysisBudget(100)
    rescanner = Rescanner(budget, Substituter("a b"), ReplacementCache())
    rescanner.expand(significant("a b"), Environment())
    assert rescanner.current_offset == 2


def test_rescan_spends_budget():
    with pytest.raises(AnalysisLimitExceeded):
        expand("I(I(I(1)))", macro("I", "x", ("x",)), limit=5)


# Stage 5: output


def test_locator_converts_offsets_to_one_based_locations():
    locator = SourceLocator("ab\ncd\r\nef")
    assert locator.location(0) == SourceLocation(1, 1)
    assert locator.location(4) == SourceLocation(2, 2)
    assert locator.location(7) == SourceLocation(3, 1)


def generated(text, start, end, kind="identifier"):
    return Token(text, kind, start, end, generated=True)


def test_merged_edits_join_overlapping_invocations():
    source = "F(1) G\n"
    expanded = [generated("a", 0, 4), generated("b", 2, 4), Token("c", "identifier", 5, 6)]
    assert merged_edits(source, expanded) == [(0, 4, " a b ")]


def test_merged_edits_keep_separate_uses_apart_and_keep_line_breaks():
    source = "F(\n1) x G\n"
    expanded = [generated("a", 0, 5), Token("x", "identifier", 6, 7), generated("b", 8, 9)]
    assert merged_edits(source, expanded) == [(0, 5, " a \n"), (8, 9, " b ")]


def test_merged_edits_drop_empty_placemarkers_from_the_text():
    expanded = [generated("a", 0, 3), generated("", 0, 3, "empty")]
    assert merged_edits("F()", expanded) == [(0, 3, " a ")]


def test_render_maps_untouched_and_expanded_spans():
    source = "x F y"
    text, mappings = render(source, [(2, 3, " 1 ")], SourceLocator(source))
    assert text == "x  1  y"
    assert [(m.output_start, m.output_end, m.source_start, m.source_end) for m in mappings] == [
        (0, 2, 0, 2),
        (2, 5, 2, 3),
        (5, 7, 3, 5),
    ]
    assert [m.expanded for m in mappings] == [False, True, False]
    assert isinstance(mappings[0], SourceMapping)
    assert mappings[1].start == SourceLocation(1, 3)


def test_render_without_edits_is_one_identity_mapping():
    text, mappings = render("abc", [], SourceLocator("abc"))
    assert text == "abc"
    assert len(mappings) == 1 and not mappings[0].expanded


# The facade keeps its surface


def test_expansion_facade_buffers_lines_and_renders():
    source = "F(\n1)\n"
    expansion = Expansion(source, AnalysisBudget(1000))
    environment = Environment(macro("F", "x + x", ("x",)))
    expansion.line(0, 3)
    expansion.line(3, 6)
    expansion.flush(environment)
    text, mappings = expansion.render(source)
    assert text.split() == ["1", "+", "1"]
    assert any(m.expanded for m in mappings)
    assert expansion.location(3).line == 2


def test_expansion_facade_forwards_current_offset_to_the_rescanner():
    expansion = Expansion("a", AnalysisBudget(10))
    expansion.current_offset = 7
    assert expansion.rescanner.current_offset == 7
    assert expansion.current_offset == 7


def test_expansion_facade_rejects_a_call_crossing_a_directive():
    source = "F\n(1)\n"
    expansion = Expansion(source, AnalysisBudget(1000))
    environment = Environment(macro("F", "x", ("x",)))
    expansion.line(0, 2)
    expansion.flush(environment)
    expansion.line(2, 6)
    with pytest.raises(ExpansionError, match="crossing a preprocessing directive"):
        expansion.flush(environment)
