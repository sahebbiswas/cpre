"""Regression tests for issue #150: a comment that spans lines belongs to its directive.

In C a comment becomes one space (translation phase 3) before directives are
processed (phase 4), so a ``/* ... */`` comment that starts on a directive line and
ends on a later line is part of that directive, together with the text after the
comment. A ``//`` comment continues over a backslash-newline the same way. Physical
line numbers must stay stable: the directive is masked line for line.
"""

import re

import pytest

import cpre
from cpre.parser import DIRECTIVE_PREFIX, logical_lines, strip_comments


def _preprocessed(source: str, **kwargs) -> str:
    result = cpre.preprocess_source(source, **kwargs)
    assert result.complete, result.incomplete
    assert result.source is not None
    # Expansion changes line lengths but never the line breaks, so line numbers are stable.
    assert re.findall(r"\r\n|\r|\n", result.source) == re.findall(r"\r\n|\r|\n", source)
    return result.source


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\s\w]", text)


# --- logical_lines() ---------------------------------------------------------------


def test_logical_line_joins_a_directive_with_the_lines_of_its_comment() -> None:
    source = "#define X 1 /* c\n  more */ + 2\nX\n"
    first, second = list(logical_lines(source))

    assert first.start_line == 1
    assert first.text.split() == ["#define", "X", "1", "+", "2"]
    assert len(first.locations) == len(first.text)
    plus = first.locations[first.text.index("+")]
    assert (plus.line, plus.column) == (2, 11)
    assert first.locations[first.text.index("X")].line == 1
    assert (second.start_line, second.text) == (3, "X")


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_logical_line_follows_a_comment_across_several_lines(ending: str) -> None:
    source = ending.join(["#if 1 /* a", "b", "c */ && 0", "text", ""])
    first, second = list(logical_lines(source))

    assert (first.start_line, second.start_line) == (1, 4)
    assert first.text.split() == ["#if", "1", "&&", "0"]
    assert first.locations[first.text.index("&&")].line == 3


def test_logical_line_mixes_backslash_and_comment_continuation() -> None:
    source = "#define X 1 \\\n  + /* c\n  */ 2\nX\n"
    first, second = list(logical_lines(source))

    assert first.text.split() == ["#define", "X", "1", "+", "2"]
    assert (second.start_line, second.text) == (4, "X")


def test_line_comment_runs_over_a_backslash_newline() -> None:
    source = "#define X 1 // a line comment \\\n  that goes on\nX\n"
    first, second = list(logical_lines(source))

    assert first.text.split() == ["#define", "X", "1"]
    assert (second.start_line, second.text) == (3, "X")


def test_hash_after_a_multi_line_comment_starts_a_directive_only_at_line_start() -> None:
    after_code = list(logical_lines("int a; /* c\n*/ #define Y 1\nY\n"))
    assert [line.start_line for line in after_code] == [1, 3]
    assert re.match(DIRECTIVE_PREFIX, after_code[0].text) is None

    at_start = list(logical_lines("/* c\n*/ #define Y 1\nY\n"))
    assert [line.start_line for line in at_start] == [1, 3]
    assert re.match(DIRECTIVE_PREFIX, at_start[0].text) is not None


@pytest.mark.parametrize(
    "source",
    [
        "#if 1 /* c\n*/",
        "#if 1 /* c\n*/\n",
        "#define X // c \\\n",
        "#define X 1 /* a\n\n\n*/ + 2",
    ],
)
def test_comment_that_ends_the_source_does_not_break_logical_lines(source: str) -> None:
    lines = list(logical_lines(source))

    assert lines[0].start_line == 1
    assert all(len(line.locations) == len(line.text) for line in lines)


def test_strip_comments_keeps_every_position_and_line_break() -> None:
    source = "a /* c\nd */ b // e \\\nf\n\"/* s */\" 'x'\n"
    stripped = strip_comments(source)

    assert len(stripped) == len(source)
    assert [i for i, char in enumerate(stripped) if char in "\r\n"] == [
        i for i, char in enumerate(source) if char in "\r\n"
    ]
    lines = stripped.split("\n")
    assert [line.strip() for line in lines[:3]] == ["a", "b", ""]
    assert lines[3] == "\"/* s */\" 'x'"


# --- preprocess_source() -----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#define X 1 /* c\n  more */ + 2\nX\n", ["1", "+", "2"]),
        ("#define X 1 /* a\nb\nc */ + 2\nX\n", ["1", "+", "2"]),
        ("#define X 1 // c \\\n  more\nX\n", ["1"]),
        ("#define X 1 /* c \\\n  more */ + 2\nX\n", ["1", "+", "2"]),
        ("#define X 1 /* c\r\n  more */ + 2\r\nX\r\n", ["1", "+", "2"]),
    ],
)
def test_define_continues_past_a_comment_that_spans_lines(source: str, expected: list[str]) -> None:
    output = _preprocessed(source)

    assert _tokens(output) == expected
    # The directive is masked, so the use stays on the physical line it was written on.
    assert _tokens(re.split(r"\r\n|\r|\n", output.rstrip("\r\n"))[-1]) == expected


def test_function_like_macro_keeps_a_body_that_follows_a_multi_line_comment() -> None:
    source = "#define ADD(a, b) /* adds\n   two */ ((a) + (b))\nint t = ADD(1, 2);\n"

    assert _tokens(_preprocessed(source)) == _tokens("int t = ((1) + (2));")


def test_if_condition_includes_the_text_after_a_multi_line_comment() -> None:
    source = "#if 1 /* c\n  more */ && 0\nyes\n#else\nno\n#endif\n"

    assert _tokens(_preprocessed(source)) == ["no"]


def test_elif_condition_includes_the_text_after_a_multi_line_comment() -> None:
    source = (
        "#if 0\na\n#elif 1 /* c\n  more */ && 0\nb\n#elif 1 /* c\n */ && 1\nc\n#else\nd\n#endif\n"
    )

    assert _tokens(_preprocessed(source)) == ["c"]


def test_undef_with_a_multi_line_comment_keeps_line_numbers() -> None:
    source = (
        "#define X 1\n#undef X /* c\n  more */\n#ifdef X\nyes\n#else\nno\n#endif\n"
        "int l = __LINE__;\n"
    )
    output = _preprocessed(source)

    assert _tokens(output) == ["no", "int", "l", "=", "9", ";"]
    assert output.splitlines()[6].strip() == "no"


def test_hash_after_a_comment_that_follows_code_is_not_a_directive() -> None:
    source = "int a; /* c\n*/ #define Y 1\nY\n"

    # Y is never defined, so every line is ordinary text and comes out as written.
    assert _preprocessed(source) == source


def test_hash_after_a_comment_that_starts_the_line_is_a_directive() -> None:
    source = "/* c\n*/ #define Y 1\nY\n"
    output = _preprocessed(source)

    assert _tokens(output) == ["1"]
    assert _tokens(output.splitlines()[2]) == ["1"]


def test_standard_line_numbers_after_a_multi_line_directive_are_physical() -> None:
    source = (
        "int a = __LINE__;\n#if 1 /* c\n*/ && 1\nint b = __LINE__;\n#endif\nint c = __LINE__;\n"
    )

    assert _tokens(_preprocessed(source)) == _tokens("int a = 1; int b = 4; int c = 6;")


def test_removed_lines_cover_every_physical_line_of_a_comment_spanned_directive() -> None:
    result = cpre.preprocess_source("#if 1 /* c\n*/ && 1\nkept\n#endif\n")

    assert result.complete
    assert result.removed_lines == frozenset({1, 2, 4})
    assert cpre.compact(result) == "kept\n"


def test_pragma_payload_ignores_a_multi_line_comment() -> None:
    seen: list[tuple[str, int]] = []

    def handler(pragma: cpre.Pragma) -> cpre.PragmaDisposition:
        seen.append((pragma.payload, pragma.location.line))
        return cpre.PragmaDisposition.CONSUME

    source = "#pragma omp /* c\n  spans */ parallel for\nint x;\n"
    output = _preprocessed(source, pragma_handler=handler)

    assert seen == [("omp parallel for", 1)]
    assert _tokens(output) == ["int", "x", ";"]


# --- analyze_source() and cover_branches() -----------------------------------------


def test_analyze_source_reads_the_whole_condition_of_a_comment_spanned_directive() -> None:
    analysis = cpre.analyze_source("#if B /* c\n*/ || B\nint y;\n#endif\n")

    assert analysis.complete
    (finding,) = analysis.findings
    assert finding.kind is cpre.FindingKind.SIMPLIFIABLE_CONDITION
    assert finding.location.line == 1
    assert finding.original_condition.split() == ["B", "||", "B"]
    assert finding.edit is not None
    assert finding.edit.replacement == "B"
    edit_range = finding.edit.range
    assert (edit_range.start.line, edit_range.start.column) == (1, 5)
    assert (edit_range.end.line, edit_range.end.column) == (2, 8)


def test_analyze_source_keeps_a_trailing_comment_out_of_the_edit() -> None:
    (finding,) = cpre.analyze_source("#if A && A /* a\n   b */\nint x;\n#endif\n").findings

    assert finding.original_condition == "A && A"
    assert finding.edit is not None
    assert (finding.edit.range.start.line, finding.edit.range.end.line) == (1, 1)


def test_cover_branches_numbers_the_branches_after_a_comment_spanned_directive() -> None:
    coverage = cpre.cover_branches("#if A /* c\n*/ && B\nint x;\n#else\nint y;\n#endif\n")

    assert coverage.complete
    assert [(o.group_line, o.branch_line, o.directive) for o in coverage.outcomes] == [
        (1, 1, "if"),
        (1, 4, "else"),
    ]


# --- parse_conditionals() ----------------------------------------------------------


def test_parse_conditionals_ends_the_directive_after_the_comment() -> None:
    source = "#if 1 /* c\n  more */ && 0\nyes\n#endif\n"
    tree = cpre.parse_conditionals(source)

    assert tree.diagnostics == ()
    if_directive, endif = tree.directives
    assert if_directive.kind == "if"
    assert (if_directive.source_range.start.line, if_directive.source_range.end.line) == (1, 3)
    assert if_directive.condition_text == "1 /* c\n  more */ && 0"
    assert if_directive.logical_condition is not None
    assert if_directive.logical_condition.split() == ["1", "&&", "0"]
    assert if_directive.condition == cpre.Conjunction((cpre.Constant(True), cpre.Constant(False)))
    assert [token.text for token in if_directive.tokens] == ["1", "&&", "0"]
    assert [token.source_range.start.line for token in if_directive.tokens] == [1, 2, 2]
    assert endif.source_range.start.line == 4

    (block,) = tree.blocks
    (branch,) = block.branches
    assert branch.body_range.start.line == 3
    assert branch.body_range.text(source) == "yes\n"


def test_parse_conditionals_reads_elif_else_and_endif_across_comments() -> None:
    source = "#if A\na\n#elif B /* c\n*/ && C\nb\n#else /* c\n*/\nc\n#endif /* c\n*/\n"
    tree = cpre.parse_conditionals(source)

    assert tree.diagnostics == ()
    assert [(d.kind, d.source_range.start.line) for d in tree.directives] == [
        ("if", 1),
        ("elif", 3),
        ("else", 6),
        ("endif", 9),
    ]
    elif_directive = tree.directives[1]
    assert elif_directive.logical_condition is not None
    assert elif_directive.logical_condition.split() == ["B", "&&", "C"]
    assert elif_directive.source_range.end.line == 5


def test_parse_conditionals_reports_text_after_a_multi_line_comment_on_else() -> None:
    tree = cpre.parse_conditionals("#if 1\n#else /* c\n*/ junk\n#endif\n")

    (diagnostic,) = tree.diagnostics
    assert diagnostic.code is cpre.StructureDiagnosticCode.TRAILING_DIRECTIVE_TEXT
    assert diagnostic.source_range.start.line == 3


def test_parse_conditionals_hash_after_a_comment_that_follows_code_is_text() -> None:
    tree = cpre.parse_conditionals("int a; /* c\n*/ #if 1\n#endif\n")

    assert [directive.kind for directive in tree.directives] == ["endif"]
    (diagnostic,) = tree.diagnostics
    assert diagnostic.code is cpre.StructureDiagnosticCode.UNMATCHED_DIRECTIVE


def test_parse_conditionals_hash_after_a_comment_that_starts_the_line_is_a_directive() -> None:
    tree = cpre.parse_conditionals("/* c\n*/ #if 1\n#endif\n")

    assert tree.diagnostics == ()
    assert [(d.kind, d.source_range.start.line) for d in tree.directives] == [
        ("if", 1),
        ("endif", 3),
    ]


def test_parse_conditionals_keeps_crlf_offsets_inside_a_comment() -> None:
    source = "#if 1 /* c\r\n*/ && 0\r\nx\r\n#endif\r\n"
    tree = cpre.parse_conditionals(source)

    (block,) = tree.blocks
    assert block.source_range.text(source) == source
    assert tree.directives[0].source_range.text(source) == "#if 1 /* c\r\n*/ && 0\r\n"
    assert tree.directives[0].condition_text == "1 /* c\r\n*/ && 0"


# --- other readers of the same line model ------------------------------------------


def test_unknown_macros_reads_both_sides_of_a_comment_spanned_condition() -> None:
    found = cpre.unknown_macros_in_source("#if FOO /* c\n */ && BAR\nint x;\n#endif\n")

    assert sorted(item.name for item in found) == ["BAR", "FOO"]
    assert all(location.line == 1 for item in found for location in item.locations)


def test_macro_analysis_reads_the_replacement_after_a_multi_line_comment() -> None:
    (result,) = cpre.analyze_macros("#define G /* a\n b */ (A && A)\n")

    assert result.original_replacement == "(A && A)"
    assert result.definition.location is not None
    assert result.definition.location.line == 1
    assert result.replacement_range is not None
    assert result.replacement_range.start.line == result.replacement_range.end.line == 2


def test_macro_rewrite_keeps_a_multi_line_comment() -> None:
    source = "#define A 1\n#define G /* a\n b */ (A && A)\nint v = G;\n"
    rewritten = cpre.rewrite_macros(source)

    assert rewritten != source
    assert rewritten.startswith("#define A 1\n#define G /* a\n b */ (")
    assert rewritten.endswith(")\nint v = G;\n")


def test_configuration_from_source_reads_a_definition_after_a_multi_line_comment() -> None:
    multi = cpre.MacroConfiguration.from_source("#define ONE 1 /* c\n spans */ + 1\n")
    single = cpre.MacroConfiguration.from_source("#define ONE 1 /* c spans */ + 1\n")

    assert multi.definitions == single.definitions
