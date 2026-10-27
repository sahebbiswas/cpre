"""Operator-precedence helpers shared by macro analysis and the evaluation trace."""

from __future__ import annotations

import pytest

from cpre._precedence import (
    top_level_binary_operators,
    use_site_conflict,
    weakest_operator,
)
from cpre.macro_analysis import _has_top_level_binary_operator


def texts(text: str) -> list[str]:
    return text.split()


@pytest.mark.parametrize(
    ("replacement", "operators"),
    [
        ("A + B", ["+"]),
        ("( A + B )", []),
        ("- A + B", ["+"]),
        ("A * - B", ["*"]),
        ("A - - B", ["-"]),
        ("! A || B", ["||"]),
        ("A ? B : C", ["?"]),
        ("( A ) + ( B )", ["+"]),
        ("f ( A , B ) * 2", ["*"]),
        ("1", []),
    ],
)
def test_top_level_binary_operators(replacement, operators):
    assert top_level_binary_operators(texts(replacement)) == operators


def test_the_weakest_operator_decides_the_grouping():
    assert weakest_operator(["*", "+", "<<"]) == "<<"
    assert weakest_operator(["+", "?", "&&"]) == "?"
    assert weakest_operator([]) is None


@pytest.mark.parametrize(
    "replacement", ["A || B", "( A || B )", "A && B", "A + B", "! A", "A || ( B && C )", ""]
)
def test_the_boolean_classification_keeps_its_meaning(replacement):
    expected = any(op in ("&&", "||") for op in top_level_binary_operators(texts(replacement)))
    assert _has_top_level_binary_operator(replacement) is expected


@pytest.mark.parametrize(
    ("replacement", "before", "after", "expected"),
    [
        ("A + B", [], "*", ("+", "*", "after")),
        ("A + B", ["*"], None, ("+", "*", "before")),
        ("A + B", ["x", "-"], None, ("+", "-", "before")),  # A - B + C regroups: right operand
        ("A - B", ["x", "-"], None, ("-", "-", "before")),
        ("A + B", ["x", "+"], None, None),  # associative at equal precedence
        ("A + B", ["x", "+"], "+", None),
        ("A + B", [], "-", None),  # left operand of a left-associative operator
        ("A * B", [], "+", None),  # tighter replacement, looser neighbour
        ("A * B", ["x", "+"], None, None),
        ("A + B", ["(", "-"], None, ("+", "-", "before")),  # a prefix minus
        ("A + B", ["-"], None, ("+", "-", "before")),  # a minus at the start is a prefix
        ("A + B", ["!"], None, ("+", "!", "before")),
        ("A + B", ["~"], None, ("+", "~", "before")),
        ("A ? B : C", [], "+", ("?", "+", "after")),
        ("A || B", [], "&&", ("||", "&&", "after")),
        ("A && B", ["||"], None, None),
        ("( A + B )", ["*"], "*", None),
        ("1", ["*"], "*", None),
        ("A + B", ["("], ")", None),
        ("A + B", [], None, None),
        # A truncating operator regroups with an equal-precedence neighbour: 2 * (7 / 2) is 6,
        # 2 * 7 / 2 is 7
        ("A / B", ["x", "*"], None, ("/", "*", "before")),
        ("A % B", ["x", "*"], None, ("%", "*", "before")),
        ("A * B / C", ["x", "*"], None, ("*", "*", "before")),
        ("A * B", ["x", "*"], None, None),  # multiplication alone is associative
        ("A / B", [], "*", None),  # as a left operand it groups as it reads
        ("A + B", ["x", "+"], None, None),
    ],
)
def test_use_site_conflicts(replacement, before, after, expected):
    assert use_site_conflict(texts(replacement), before, after) == expected
