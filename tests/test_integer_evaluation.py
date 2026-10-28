"""The integer-value evaluator shares its grammar and bounds with #if conditions."""

from dataclasses import replace

import pytest

from cpre.expansion import Expansion
from cpre.macros import MacroDefinition, MacroEnvironment
from cpre.numeric_conditions import (
    FailureKind,
    IntegerEvaluation,
    NumericConditionError,
    evaluate_integer_expression,
    evaluate_numeric_condition,
)
from cpre.robdd import AnalysisBudget

INT64_MAX = 9223372036854775807
INT64_MIN = -INT64_MAX - 1
UINT64_MAX = (1 << 64) - 1


def environment(*definitions):
    result = MacroEnvironment()
    for definition in definitions:
        result.define(definition)
    return result


def obj(name, replacement):
    return MacroDefinition(name, replacement)


def evaluate(text, *definitions, limit=100_000, expansion_limit=100_000, keep_expanded=False):
    result = evaluate_integer_expression(
        text,
        environment(*definitions),
        Expansion(text, AnalysisBudget(expansion_limit)),
        AnalysisBudget(limit),
    )
    return result if keep_expanded else replace(result, expanded=None)


def condition(text, *definitions):
    return evaluate_numeric_condition(
        text,
        environment(*definitions),
        Expansion(text, AnalysisBudget(100_000)),
        AnalysisBudget(100_000),
    )


@pytest.mark.parametrize(
    "text, value",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("10 - 4 - 3", 3),
        ("7 / 2", 3),
        ("7 % 4", 3),
        ("-7 / 2", -3),
        ("-7 % 3", -1),
        ("7 % -3", 1),
        ("7 / -2", -3),
        ("-7 / -2", 3),
        ("- - 4", 4),
        ("+5", 5),
        ("1 << 4", 16),
        ("0xff & 0x0f", 15),
        ("3 > 2", 1),
        ("1 ? 10 : 20", 10),
        ("0x10 + 020 + 0b11", 16 + 16 + 3),
        ("1'000 + 1", 1001),
        (str(INT64_MAX), INT64_MAX),
        (f"(-{INT64_MAX} - 1)", INT64_MIN),
    ],
)
def test_signed_values(text, value):
    result = evaluate(text)
    assert result == IntegerEvaluation(value=value)
    assert result.concrete
    assert result.unsigned is False


@pytest.mark.parametrize(
    "text, value",
    [
        ("1u + 2", 3),
        ("0xffffffffffffffffu + 1u", 0),
        ("-1 + 0u", UINT64_MAX),
        ("0u - 1", UINT64_MAX),
        ("~0u", UINT64_MAX),
        ("0xffffffffffffffffu / 2u", UINT64_MAX // 2),
        ("0xffffffffffffffffu % 10u", UINT64_MAX % 10),
        ("1 ? 1u : -1", 1),
        ("0x8000000000000000", 1 << 63),
    ],
)
def test_unsigned_values_wrap_and_convert(text, value):
    result = evaluate(text)
    assert result == IntegerEvaluation(value=value, unsigned=True)


@pytest.mark.parametrize(
    "text, kind",
    [
        ("1 / 0", FailureKind.DIVISION_BY_ZERO),
        ("1 % 0", FailureKind.DIVISION_BY_ZERO),
        ("1u / 0u", FailureKind.DIVISION_BY_ZERO),
        ("1u % 0u", FailureKind.DIVISION_BY_ZERO),
        (f"{INT64_MAX} + 1", FailureKind.OVERFLOW),
        (f"(-{INT64_MAX} - 1) - 1", FailureKind.OVERFLOW),
        (f"{INT64_MAX} * 2", FailureKind.OVERFLOW),
        (f"-(-{INT64_MAX} - 1)", FailureKind.OVERFLOW),
        (f"(-{INT64_MAX} - 1) / -1", FailureKind.OVERFLOW),
        ("9223372036854775808", FailureKind.OVERFLOW),
        ("0x10000000000000000", FailureKind.OVERFLOW),
        ("1 << 64", FailureKind.OVERFLOW),
        ("1 << -1", FailureKind.OVERFLOW),
        ("1 << 63 + 0", FailureKind.OVERFLOW),
        ("'ab'", FailureKind.IMPLEMENTATION_DEFINED),
        ("'\\xff' + 1", FailureKind.IMPLEMENTATION_DEFINED),
        ("'a' + 1", FailureKind.IMPLEMENTATION_DEFINED),
        ("1 +", FailureKind.UNSUPPORTED),
        ("1 2", FailureKind.UNSUPPORTED),
        ('"text"', FailureKind.UNSUPPORTED),
        ("1.5", FailureKind.UNSUPPORTED),
        ("(1 + 2", FailureKind.UNSUPPORTED),
        ("UNKNOWN + 1", FailureKind.UNRESOLVED_IDENTIFIER),
        ("defined(UNKNOWN)", FailureKind.UNRESOLVED_IDENTIFIER),
        ("(" * 40 + "1" + ")" * 40, FailureKind.NESTING_BOUND),
    ],
)
def test_failures_are_results_with_a_kind(text, kind):
    result = evaluate(text)
    assert result.failure is kind, result
    assert result.value is None
    assert not result.concrete
    assert result.message


def test_failure_offsets_point_at_the_offending_token():
    assert evaluate("1 + UNKNOWN").offset == 4
    assert evaluate("1 2").offset == 2
    assert evaluate("1 + defined(UNKNOWN)").offset == 4
    assert evaluate("1 + ('ab' * 2) + 3").offset == 5
    assert evaluate("'ab'").offset == 0


def test_failure_messages_name_what_was_not_resolved():
    assert "'UNKNOWN'" in evaluate("1 + UNKNOWN").message
    assert "'UNKNOWN'" in evaluate("defined(UNKNOWN)").message
    assert "division by zero" in evaluate("1 / 0").message
    assert "'ab'" in evaluate("'ab'").message


@pytest.mark.parametrize("digits", [5000, 100_000])
def test_huge_decimal_literals_are_overflow_not_exceptions(digits):
    text = "9" * digits
    result = evaluate(text)
    assert result.failure is FailureKind.OVERFLOW
    with pytest.raises(NumericConditionError) as raised:
        condition(text)
    assert raised.value.kind is FailureKind.OVERFLOW


@pytest.mark.parametrize(
    "text, value",
    [
        ("0 && (1 / 0)", 0),
        ("1 || (1 / 0)", 1),
        ("1 ? 2 : 1 / 0", 2),
        ("0 ? 1 / 0 : 3", 3),
        (f"1 || ({INT64_MAX} + 1)", 1),
        (f"0 ? ({INT64_MAX} * 2) : 5", 5),
        ("0 ? 1 << 99 : 6", 6),
    ],
)
def test_untaken_branches_do_not_fail(text, value):
    assert evaluate(text) == IntegerEvaluation(value=value)


def test_macros_expand_before_evaluation():
    assert evaluate(
        "AREA", obj("W", "3"), obj("H", "4"), obj("PAD", "2"), obj("AREA", "(W * H + PAD)")
    ) == IntegerEvaluation(value=14)


def test_unparenthesized_replacement_follows_use_site_precedence():
    definitions = (obj("A", "1"), obj("B", "2"), obj("X", "A + B"))
    assert evaluate("X * 2", *definitions) == IntegerEvaluation(value=5)
    assert evaluate("(X) * 2", *definitions) == IntegerEvaluation(value=6)


def test_function_like_macro_calls_evaluate():
    square = MacroDefinition("SQ", "((x) * (x))", ("x",))
    assert evaluate("SQ(2 + 3)", square) == IntegerEvaluation(value=25)
    assert evaluate("SQ(SQ(2))", square) == IntegerEvaluation(value=16)


def test_defined_operator_with_settled_names():
    assert evaluate("defined(A) * 5 + 1", obj("A", "")) == IntegerEvaluation(value=6)


def test_parser_budget_exhaustion_still_propagates():
    from cpre.robdd import AnalysisLimitExceeded

    # Only the parser's budget is small, so it is the parser that runs out.
    with pytest.raises(AnalysisLimitExceeded):
        evaluate("1 + 1 + 1 + 1 + 1 + 1 + 1 + 1", limit=3)
    assert evaluate("1 + 1 + 1 + 1 + 1 + 1 + 1 + 1", limit=1000).value == 8


def test_expansion_budget_exhaustion_still_propagates():
    from cpre.robdd import AnalysisLimitExceeded

    with pytest.raises(AnalysisLimitExceeded):
        evaluate("A", obj("A", "1 + 1 + 1 + 1 + 1 + 1"), expansion_limit=3)


@pytest.mark.parametrize(
    "text",
    [
        "1 + 2 * 3 == 7",
        "-7 % 3 == -1",
        "7 % -3 == 1",
        "-1 > 0u",
        "0xffffffffffffffffu + 1u",
        "0 && (1 / 0)",
        "1 || (1 / 0)",
        "1 ? 2 : 0",
        "0",
        "(1 << 3) - 8",
        "'a'",
        "'ab'",
        "UNKNOWN",
        "1 / 0",
        f"{INT64_MAX} + 1",
        "1 +",
        "defined(UNKNOWN)",
        "(" * 40 + "1" + ")" * 40,
    ],
)
def test_condition_and_value_evaluators_agree(text):
    """The condition evaluator is the value evaluator's truth, errors included."""
    value = evaluate(text)
    try:
        truth = condition(text)
    except NumericConditionError as error:
        assert value.failure is error.kind
        assert value.message == str(error)
        assert value.offset == error.offset
        return
    if truth is None:
        assert value.failure is FailureKind.UNRESOLVED_IDENTIFIER
    elif value.concrete:
        assert truth is (value.value != 0)
    else:
        # A character constant can have a known truth without a known value.
        assert value.failure is FailureKind.IMPLEMENTATION_DEFINED


@pytest.mark.parametrize(
    "text, definitions, expanded",
    [
        ("1+2 *3", (), "1 + 2 * 3"),
        ("( 1+2 )*3", (), "(1 + 2) * 3"),
        ("-1 + - -2", (), "-1 + --2"),
        ("1-(-2)", (), "1 - (-2)"),
        ("!0 && ~1", (), "!0 && ~1"),
        ("1?2:3", (), "1 ? 2 : 3"),
        ("AREA", (obj("W", "3"), obj("AREA", "(W * 2)")), "(3 * 2)"),
        ("X * 2", (obj("A", "1"), obj("B", "2"), obj("X", "A+B")), "1 + 2 * 2"),
        ("1 /* note */ + 2", (), "1 + 2"),
    ],
)
def test_expanded_text_is_the_readable_expansion(text, definitions, expanded):
    assert evaluate(text, *definitions, keep_expanded=True).expanded == expanded


def test_expanded_text_is_kept_for_failures_after_expansion_only():
    assert evaluate("UNK + 1", keep_expanded=True).expanded == "UNK + 1"
    assert evaluate("1 / 0", keep_expanded=True).expanded == "1 / 0"
    assert evaluate("defined(UNK) + 1", keep_expanded=True).expanded == "defined(UNK) + 1"
