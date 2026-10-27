"""An unresolved name in an operand C never evaluates does not make a result unresolved (#224)."""

from __future__ import annotations

import pytest

import cpre
from cpre.expansion import Expansion
from cpre.macros import MacroEnvironment
from cpre.numeric_conditions import FailureKind, evaluate_integer_expression
from cpre.robdd import AnalysisBudget

OPEN = cpre.MacroConfiguration()
SKIPPED = [
    ("1 || UNKNOWN", 1),
    ("0 && UNKNOWN", 0),
    ("1 ? 2 : UNKNOWN", 2),
    ("0 ? UNKNOWN : 3", 3),
    ("1 || defined(UNKNOWN)", 1),
    ("0 && defined UNKNOWN", 0),
    ("0 && (UNKNOWN)", 0),
    ("0 ? 1 : 0 ? UNKNOWN : 7", 7),
]
NEEDED = [
    "UNKNOWN || 1",
    "1 && UNKNOWN",
    "0 || UNKNOWN",
    "UNKNOWN ? 1 : 2",
    "1 ? UNKNOWN : 2",
    "defined(UNKNOWN) || 1",
    "1 || UNKNOWN + 1",
    "0 && 2 * UNKNOWN",
    "0 && UNKNOWN == 1",
    "1 || !UNKNOWN",
    "1 || F(1, 2)",
]


@pytest.mark.parametrize(("text", "value"), SKIPPED)
def test_value_evaluator_is_concrete(text, value):
    result = cpre.evaluate_expression(text, OPEN)
    assert (result.concrete, result.value, result.unresolved) == (True, value, ())


@pytest.mark.parametrize("text", NEEDED)
def test_value_evaluator_stays_unresolved_when_the_name_matters(text):
    assert cpre.evaluate_expression(text, OPEN).state is cpre.EvaluationState.SYMBOLIC


def _internal(text):
    return evaluate_integer_expression(
        text,
        MacroEnvironment(),
        Expansion(text, AnalysisBudget(100_000)),
        AnalysisBudget(100_000),
    )


@pytest.mark.parametrize(("text", "value"), SKIPPED)
def test_internal_evaluator_returns_the_value(text, value):
    result = _internal(text)
    assert (result.value, result.failure) == (value, None)


def test_internal_evaluator_still_reports_the_unresolved_name():
    assert _internal("UNKNOWN || 1").failure is FailureKind.UNRESOLVED_IDENTIFIER


@pytest.mark.parametrize(("text", "value"), SKIPPED)
def test_preprocess_source_selects_the_branch(text, value):
    result = cpre.preprocess_source(f"#if {text}\nyes\n#else\nno\n#endif\n", configuration=OPEN)
    assert result.complete and result.source is not None
    assert ("yes" in result.source) is bool(value)
    assert ("no" in result.source) is not bool(value)


@pytest.mark.parametrize(("text", "value"), SKIPPED)
def test_cover_branches_finds_one_branch_unreachable(text, value):
    result = cpre.cover_branches(f"#if {text}\nyes\n#else\nno\n#endif\n")
    assert result.complete
    first, second = result.outcomes[:2]
    reachable, other = (first, second) if value else (second, first)
    assert reachable.status is cpre.BranchOutcomeStatus.COVERED
    assert other.status is not cpre.BranchOutcomeStatus.COVERED


@pytest.mark.parametrize("text", ["UNKNOWN || 1", "1 && UNKNOWN"])
def test_preprocess_source_with_a_needed_name_is_decided_only_when_it_is(text):
    result = cpre.preprocess_source(f"#if {text}\nyes\n#endif\n", configuration=OPEN)
    assert result.complete is (text == "UNKNOWN || 1")


def test_an_expansion_to_extra_tokens_is_not_set_aside():
    # An unconfigured macro could expand to `+ 1`, so the grouping is not known.
    assert cpre.evaluate_expression("1 || UNKNOWN + 1", OPEN).state is cpre.EvaluationState.SYMBOLIC
