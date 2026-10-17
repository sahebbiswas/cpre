"""The lexical front end is the one definition of splices, comments, literals and directives.

Covers `cpre.lexer` directly, the backslash-and-blanks splice policy (issue #157), and
that the parser, the structural parser and the core preprocessor agree on where each
directive is (issue #162).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import cpre
from cpre import lexer
from cpre.errors import CpreError
from cpre.lexer import (
    CONDITIONAL_DIRECTIVES,
    OffsetMap,
    directive_head,
    lex,
    logical_lines,
    macro_name,
    scan,
    splice,
    strip_comments,
    tokenize,
)
from cpre.model import DirectiveStructureError
from cpre.parser import parse_source

CASES = sorted((Path(__file__).parent / "compiler_differential" / "cases").glob("*.c"))

FRAGMENTS = [
    "#if A\n", "#ifdef B\n", "#ifndef C\n", "#elif D\n", "#else\n", "#endif\n",
    "#define M 1\n", "#define F(x) x\n", "#undef M\n", "# \n", "#pragma once\n",
    "  #  if X && Y\n", "int x;\n", "x\n", "\n", "\r\n", "\r", "\\\n", "\\ \n", "\\\t\r\n",
    "/* c */", "/*\n", "*/", "//", "// n \\\n", "// n \\ \n", "\"s\"", "\"un", "'", "'a'",
    "don't ", "1'024", "R\"x(\n#if Z\n)x\"", "R\"(", "u8\"q\"", "L'c'", "#", "%:", " ", "\t",
    "A", "(", ")", "defined(A)", "0x1p+3", "a\\\nb",
]  # fmt: skip
sources = st.lists(st.sampled_from(FRAGMENTS), max_size=14).map("".join)


def _lines_of(text: str) -> list[int]:
    return [match.end() for match in re.finditer(r"\r\n|\r|\n", text)]


# --- splices (translation phase 2) -------------------------------------------------


@pytest.mark.parametrize("tail", ["", " ", "\t", "\f", "\v", " \t "])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_splice_accepts_blanks_between_backslash_and_newline(tail, newline):
    assert splice(f"ab\\{tail}{newline}cd") == "abcd"


def test_splice_keeps_backslash_not_followed_by_a_line_break():
    assert splice("a\\b \\ c\\") == "a\\b \\ c\\"


def test_offset_map_maps_spliced_offsets_to_physical_offsets():
    source = "ab\\ \ncd\\\nef"
    mapping = OffsetMap([(2, 5), (7, 9)])
    assert [mapping.physical(i) for i in range(6)] == [0, 1, 5, 6, 9, 10]
    assert source[mapping.physical(2)] == "c"
    assert mapping.physical_end(2) == 2  # the splice after "ab" is not part of "ab"


def test_tokenize_reports_physical_offsets_across_a_splice():
    source = "id\\ \nent + 1"
    tokens = [t for t in tokenize(source) if t.kind != "space"]
    assert [t.text for t in tokens] == ["ident", "+", "1"]
    assert (tokens[0].start, tokens[0].end) == (0, 8)
    assert source[tokens[1].start] == "+"


# --- issue #157: backslash, blanks, newline ---------------------------------------


def test_157_directive_continues_over_backslash_blank_newline():
    source = "#if defined(A) && \\ \n    defined(B)\nx\n#endif\n"
    tree = cpre.parse_conditionals(source)
    assert [
        (d.kind, d.source_range.start.line, d.source_range.end.line) for d in tree.directives
    ] == [
        ("if", 1, 3),
        ("endif", 4, 5),
    ]
    assert not tree.diagnostics
    assert tree.directives[0].logical_condition == "defined(A) &&     defined(B)"
    assert [(g.line, g.end_line) for g in parse_source(source).groups] == [(1, 4)]


def test_157_macro_body_continues_over_backslash_blank_newline():
    result = cpre.preprocess_source("#define SUM(a, b) \\ \n    ((a) + (b))\nint t = SUM(1, 2);\n")
    assert result.complete
    assert re.sub(r"\s+", "", result.source) == "inttotal=((1)+(2));".replace("total", "t")


def test_157_line_comment_ending_in_backslash_blank_newline_swallows_next_line():
    source = "int a; // note \\  \nint hidden;\nint b;\n"
    result = cpre.preprocess_source(source)
    assert result.complete
    assert "hidden" in result.source  # text is passed through, but...
    # ...the front end treats "int hidden;" as part of the comment, as GCC and Clang do.
    assert strip_comments(source).split("\n")[1].strip() == ""
    assert [line.start_line for line in logical_lines(source)] == [1, 3]


def test_157_ifdef_inside_comment_continuation_is_not_a_directive():
    source = "// c \\ \n#if 1\nx\n"
    assert not cpre.parse_conditionals(source).directives
    assert parse_source(source).groups == []


# --- bugs the shared front end fixes by construction ------------------------------


def test_condition_continued_by_splice_is_one_expression_in_the_preprocessor():
    source = "#define A 1\n#define B 0\n#if A\\\nB\nyes\n#else\nno\n#endif\n"
    result = cpre.preprocess_source(source)
    # `A` and `B` are adjacent tokens, which is not a valid expression: never `A B` => 1.
    assert not result.complete or "yes" not in result.source.replace("#", "")


def test_apostrophe_in_excluded_text_does_not_hide_later_directives():
    source = "#if 0\nit's text\n#endif\nint kept;\n#if A\n#endif\n"
    tree = cpre.parse_conditionals(source)
    assert [d.kind for d in tree.directives] == ["if", "endif", "if", "endif"]
    assert not tree.diagnostics


def test_directive_inside_raw_string_is_text():
    source = 'const char *s = R"x(\n#if Z\n)x";\n#if A\n#endif\n'
    assert [d.source_range.start.line for d in cpre.parse_conditionals(source).directives] == [4, 5]
    assert [g.line for g in parse_source(source).groups] == [4]


# --- directive heads and names ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name", "operand"),
    [
        ("#if X", "if", " X"),
        (" \t#  define A", "define", " A"),
        ("#", None, ""),
        ('# 12 "f"', None, '12 "f"'),
        ("#ifdef(", "ifdef", "("),
    ],
)
def test_directive_head(text, name, operand):
    head = directive_head(text)
    assert head is not None
    assert head.name == name
    assert text[head.name_end :] == operand


@pytest.mark.parametrize("text", ["x #if", "/**/#if", "\f\v x", ""])
def test_not_a_directive(text):
    assert directive_head(text) is None


def test_macro_name():
    assert macro_name("  FOO(x) x") == "FOO"
    assert macro_name("\t_a1 2") == "_a1"
    assert macro_name("1x") is None


# --- scanner equivalence ----------------------------------------------------------


@given(sources)
@settings(max_examples=300, deadline=None)
def test_region_scanner_and_token_scanner_agree_on_comments_and_literals(source):
    text = splice(source)
    regions = [
        (m.lastgroup, m.span()) for m in lexer._REGIONS.finditer(text) if m.lastgroup != "number"
    ]
    tokens = [
        (m.lastgroup, m.span())
        for m in lexer._TOKEN.finditer(text)
        if m.lastgroup in ("raw", "literal", "comment")
    ]
    assert regions == tokens


@given(sources)
@settings(max_examples=200, deadline=None)
def test_scan_tiles_the_text(source):
    text = splice(source)
    assert "".join(token.text for token in scan(text)) == text


@given(sources)
@settings(max_examples=200, deadline=None)
def test_strip_comments_preserves_length_and_line_breaks(source):
    stripped = strip_comments(source)
    assert len(stripped) == len(source)
    assert _lines_of(stripped) == _lines_of(source)


# --- one answer for every component ----------------------------------------------


def _flatten(groups):
    for group in groups:
        for branch in group.branches:
            yield branch.directive, branch.line
            yield from _flatten(branch.children)
        yield "endif", group.end_line


def _expected(source: str):
    return [
        (line.directive.name, line.start_line)
        for line in logical_lines(source)
        if line.directive is not None and line.directive.name in CONDITIONAL_DIRECTIVES
    ]


def _assert_components_agree(source: str) -> None:
    expected = _expected(source)

    structure = [
        (d.kind, d.source_range.start.line) for d in cpre.parse_conditionals(source).directives
    ]
    assert structure == expected

    # parse_source rejects an unbalanced source; when it accepts one, every group
    # directive it reports is a directive the front end found at that line.
    try:
        groups = parse_source(source).groups
    except DirectiveStructureError:
        groups = []
    parsed = [(kind, line) for kind, line in _flatten(groups) if line is not None]
    assert set(parsed) <= set(expected)

    try:
        result = cpre.preprocess_source(source)
    except CpreError:  # an unbalanced or malformed source is rejected, never mis-split
        return
    if result.source is None:
        return
    out = re.split(r"\r\n|\r|\n", result.source)
    for line in logical_lines(source):
        head = line.directive
        if head is not None and head.name in CONDITIONAL_DIRECTIVES | {"define", "undef"}:
            for number in range(line.start_line, line.end_line + 1):
                assert out[number - 1].strip() == "", (number, out[number - 1])


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_components_agree_on_differential_cases(path):
    _assert_components_agree(path.read_text())


@given(sources)
@settings(max_examples=400, deadline=None)
def test_components_agree_on_fuzzed_sources(source):
    _assert_components_agree(source)


def test_lex_is_cached_per_source():
    assert lex("#if A\n#endif\n") is lex("#if A\n#endif\n")
