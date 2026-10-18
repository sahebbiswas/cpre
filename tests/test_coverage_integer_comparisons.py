"""``cover_branches`` solves ``NAME op INT`` comparisons with an integer value (#125)."""

from __future__ import annotations

import itertools

import pytest

import cpre
from cpre import BranchOutcomeStatus as Status
from cpre import cover_branches
from cpre._coverage_integers import parse_comparison, solve_integers
from cpre.model import DefinedVariable, Predicate, Variable

COVERED = Status.COVERED


def _values(result):
    return [
        {d.name: d.replacement for d in item.configuration.definitions}
        for item in result.configurations
    ]


def _check(source, result):
    """Every configuration selects exactly the outcomes it claims."""
    for item in result.configurations:
        selections = {}
        token = cpre.preprocessing._SELECTED_BRANCHES.set(selections)
        try:
            preprocessed = cpre.preprocess_source(source, configuration=item.configuration)
        finally:
            cpre.preprocessing._SELECTED_BRANCHES.reset(token)
        assert preprocessed.complete
        claimed = {
            (result.outcomes[i].group_line, result.outcomes[i].branch_line) for i in item.covers
        }
        assert claimed <= set(selections.items())


def test_the_example_from_the_issue_is_fully_covered():
    source = "#if VERSION >= 3\nint modern;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert [o.status for o in result.outcomes] == [COVERED, COVERED]
    assert {"VERSION": "3"} in _values(result)


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("V >= 3 && V < 5", "3"),
        ("V > 3", "4"),
        ("V == 7", "7"),
        ("V != 0 && V < 2", "-1"),
        ("V <= -2", "-3"),
        ("V == - 4", "-4"),
        ("3 <= V", "3"),
        ("V >= 0x10", "16"),
    ],
)
def test_comparisons_on_one_macro_are_combined(condition, expected):
    source = f"#if {condition}\nint x;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert result.outcomes[0].status is COVERED
    assert {"V": expected} in _values(result)


def test_elif_chain_gets_a_value_per_branch():
    source = "#if V >= 3 && V < 5\nint a;\n#elif V == 7\nint b;\n#else\nint c;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert [o.status for o in result.outcomes] == [COVERED] * 3


def test_contradictory_comparisons_are_not_covered_never_covered_wrongly():
    source = "#if V > 3 && V < 2\nint x;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    impossible, otherwise = result.outcomes
    assert impossible.status is Status.NOT_COVERED
    assert "cannot all hold" in impossible.reason
    assert otherwise.status is COVERED


def test_implied_combinations_are_not_proposed():
    # V >= 5 true with V >= 3 false is impossible, but V >= 3 && !(V >= 5) is fine.
    source = "#if V >= 3 && !(V >= 5)\nint x;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert result.outcomes[0].status is COVERED
    assert {"V": "3"} in _values(result)


def test_truthiness_and_definedness_agree_with_the_value():
    source = "#if V && V > 5\nint a;\n#elif !V\nint b;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert [o.status for o in result.outcomes][:2] == [COVERED, COVERED]


def test_independent_macros_get_independent_values():
    source = "#if A >= 2 && B < 1\nint x;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    assert result.outcomes[0].status is COVERED
    assert {"A": "2", "B": "0"} in _values(result)


def test_context_fixed_macros_are_not_assigned():
    context = cpre.PreprocessingContext(standard_macros={"__STDC_VERSION__": "201710L"})
    source = "#if __STDC_VERSION__ >= 201112L\nint x;\n#endif\n"
    result = cover_branches(source, context=context)
    assert all("__STDC_VERSION__" not in values for values in _values(result))


def test_results_are_deterministic():
    source = (
        "#if V >= 3 && V < 5\nint a;\n#elif V == 7\nint b;\n#endif\n#if W > 1\nint c;\n#endif\n"
    )
    first = cover_branches(source)
    assert _values(cover_branches(source)) == _values(first)


@pytest.mark.parametrize(
    "first, second",
    list(
        itertools.product(
            ["V > 3", "V <= 3", "V == 3", "V != 3", "V < 0", "V >= 5"],
            ["V < 2", "V >= 4", "V == 3", "V != 0", "V > 5", "V <= -1"],
        )
    ),
)
def test_covered_exactly_when_some_integer_satisfies_both(first, second):
    source = f"#if {first} && {second}\nint x;\n#endif\n"
    result = cover_branches(source)
    _check(source, result)
    possible = any(eval(f"({first}) and ({second})", {}, {"V": value}) for value in range(-8, 12))
    expected = COVERED if possible else Status.NOT_COVERED
    assert result.outcomes[0].status is expected


def test_parse_comparison_shapes():
    assert parse_comparison("VERSION >= 3") == ("VERSION", ">=", 3)
    assert parse_comparison("3 <= VERSION") == ("VERSION", ">=", 3)
    assert parse_comparison("V == - 1") == ("V", "==", -1)
    assert parse_comparison("V < 0x10") == ("V", "<", 16)
    for opaque in ["V >= 3L", "V >= 3 + 1", "(V) >= 3", "V >= W", "V < 010", "V % 2 == 1"]:
        assert parse_comparison(opaque) is None


def test_solver_leaves_undefined_macros_alone():
    atoms = {Predicate("V >= 3"): True, DefinedVariable("V"): False}
    assert solve_integers(atoms, set()) == ({}, {})
    values, contradictory = solve_integers(
        {Predicate("V > 3"): True, Predicate("V < 2"): True, Variable("W"): True}, set()
    )
    assert values == {} and set(contradictory) == {"V"}
