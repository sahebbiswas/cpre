"""A directive is reported at the line of its '#', not at a preceding splice-only line (#169)."""

import pytest

from cpre import analyze_source, preprocess_source
from cpre.errors import CpreError
from cpre.lexer import logical_lines


def test_logical_line_distinguishes_masking_start_from_first_character():
    lines = list(logical_lines("int x;\n\\\n#include <a.h>\n"))
    spliced = lines[1]
    assert (spliced.start_line, spliced.end_line) == (2, 3)
    assert spliced.first_line == 3
    assert spliced.directive_line == 3


def test_directive_line_follows_the_hash_after_a_multiline_comment():
    (line,) = list(logical_lines("/* a\n b */ #if 1\n"))
    assert line.first_line == 1
    assert line.directive_line == 2


@pytest.mark.parametrize(
    "source, expected",
    [
        ("int x;\n\\\n#include <nonexistent.h>\n", 3),
        ("\\\n#include <nonexistent.h>\n", 2),
        ("int x;\n\\\n\\\n#include <nonexistent.h>\n", 4),
    ],
)
def test_unsupported_include_is_reported_at_the_hash(source, expected):
    result = preprocess_source(source)
    assert [item.location.line for item in result.incomplete] == [expected]


def test_conditional_branch_lines_are_the_hash_lines():
    source = "int x;\n\\\n#if A\n\\\n#else\n#endif\n"
    (group,) = analyze_source(source).tree.groups
    assert group.line == 3
    assert [branch.line for branch in group.branches] == [3, 5]
    assert group.end_line == 6


def test_expression_error_is_reported_at_the_hash():
    with pytest.raises(CpreError) as raised:
        analyze_source("int a;\n\\\n#if (\n#endif\n")
    assert raised.value.location is not None and raised.value.location.line == 3


def test_masking_still_blanks_the_splice_only_line():
    result = preprocess_source("a\n\\\n#define X 1\nX\n")
    assert result.source is not None
    assert result.source.count("\n") == 4
    assert result.source.split("\n")[3].strip() == "1"
    assert result.removed_lines == frozenset({2, 3})
