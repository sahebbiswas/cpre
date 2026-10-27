"""``evaluate_expression`` and ``evaluate_macro``: values, reasons and the JSON record."""

from __future__ import annotations

import os

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import cpre
from cpre import (
    AnalysisError,
    AnalysisOptions,
    ErrorCode,
    Evaluation,
    EvaluationReason,
    EvaluationState,
    MacroConfiguration,
    MacroDefinition,
    PreprocessingContext,
    UnknownNamePolicy,
    evaluate_expression,
    evaluate_macro,
    preprocess_source,
)
from cpre.serialization import evaluation_document

INT64_MAX = 9223372036854775807
UINT64_MAX = (1 << 64) - 1

CONFIG = MacroConfiguration(
    integers={"WIDTH": 3, "HEIGHT": 4, "PAD": 2},
    definitions=[
        MacroDefinition("AREA", "(WIDTH * HEIGHT + PAD)"),
        MacroDefinition("SUM", "WIDTH + HEIGHT"),
        MacroDefinition("SQUARE", "((x) * (x))", ("x",)),
        MacroDefinition("NEGATIVE", "(-7)"),
        MacroDefinition("BIG", str(INT64_MAX)),
        MacroDefinition("EMPTY", ""),
        MacroDefinition("SELF", "SELF + 1"),
    ],
)


def test_public_names_are_exported():
    for name in (
        "Evaluation",
        "EvaluationReason",
        "EvaluationState",
        "evaluate_expression",
        "evaluate_macro",
    ):
        assert name in cpre.__all__


def test_macro_expands_to_its_value():
    result = evaluate_macro("AREA", CONFIG)
    assert result == Evaluation(
        kind="macro",
        expression="AREA",
        state=EvaluationState.CONCRETE,
        value=14,
        unsigned=False,
        expanded="(3 * 4 + 2)",
    )
    assert result.concrete


def test_expression_with_nested_and_function_like_macros():
    result = evaluate_expression("SQUARE(AREA) % 100 - NEGATIVE", CONFIG)
    assert result.concrete and result.value == (14 * 14) % 100 + 7
    assert result.expanded == "(((3 * 4 + 2)) * ((3 * 4 + 2))) % 100 - (-7)"


def test_use_site_precedence_differs_from_the_macro_alone():
    assert evaluate_macro("SUM", CONFIG).value == 7
    assert evaluate_expression("SUM * 2", CONFIG).value == 3 + 4 * 2
    assert evaluate_expression("SUM * 2", CONFIG).expanded == "3 + 4 * 2"
    assert evaluate_expression("(SUM) * 2", CONFIG).value == 14


def test_without_a_configuration_only_literals_evaluate():
    assert evaluate_expression("2 * (3 + 4)").value == 14
    result = evaluate_expression("WIDTH + 1")
    assert result.reason is EvaluationReason.UNRESOLVED_IDENTIFIER


def test_unsigned_results_say_so():
    result = evaluate_expression("0u - 1")
    assert result.value == UINT64_MAX and result.unsigned is True
    assert evaluate_expression("1 - 2").unsigned is False


@pytest.mark.parametrize(
    "text, value",
    [
        ("-7 / 2", -3),
        ("-7 % 3", -1),
        ("7 % -3", 1),
        ("NEGATIVE / 2", -3),
        ("NEGATIVE % 3", -1),
    ],
)
def test_division_and_remainder_truncate_toward_zero(text, value):
    assert evaluate_expression(text, CONFIG).value == value


@pytest.mark.parametrize(
    "text, reason",
    [
        ("1 / 0", EvaluationReason.DIVISION_BY_ZERO),
        ("WIDTH % (HEIGHT - 4)", EvaluationReason.DIVISION_BY_ZERO),
        ("BIG + 1", EvaluationReason.OVERFLOW),
        ("BIG * 2", EvaluationReason.OVERFLOW),
        ("UNKNOWN + 1", EvaluationReason.UNRESOLVED_IDENTIFIER),
        ("defined(UNKNOWN)", EvaluationReason.UNRESOLVED_IDENTIFIER),
        ("1 +", EvaluationReason.UNSUPPORTED_SYNTAX),
        ('"text"', EvaluationReason.UNSUPPORTED_SYNTAX),
        ("1.5", EvaluationReason.UNSUPPORTED_SYNTAX),
        ("EMPTY", EvaluationReason.UNSUPPORTED_SYNTAX),
        ("'ab'", EvaluationReason.IMPLEMENTATION_DEFINED),
        ("(" * 40 + "1" + ")" * 40, EvaluationReason.NESTING_BOUND),
        ("SQUARE(1, 2)", EvaluationReason.EXPANSION_ERROR),
        ("SQUARE(", EvaluationReason.EXPANSION_ERROR),
        ("__LINE__", EvaluationReason.EXPANSION_ERROR),
    ],
)
def test_expression_reasons(text, reason):
    result = evaluate_expression(text, CONFIG)
    assert result.state is EvaluationState.UNEVALUABLE
    assert result.reason is reason
    assert result.value is None and result.unsigned is None
    assert not result.concrete
    assert result.message


def test_budget_exhaustion_is_a_result():
    result = evaluate_expression("1 + 1 + 1 + 1 + 1 + 1", options=AnalysisOptions(max_work=3))
    assert result.reason is EvaluationReason.BUDGET_EXCEEDED
    assert evaluate_expression("1 + 1 + 1 + 1 + 1 + 1").value == 6


@pytest.mark.parametrize(
    "name, reason",
    [
        ("SQUARE", EvaluationReason.FUNCTION_LIKE),
        ("MISSING", EvaluationReason.UNRESOLVED_IDENTIFIER),
        ("EMPTY", EvaluationReason.UNSUPPORTED_SYNTAX),
    ],
)
def test_macro_reasons(name, reason):
    result = evaluate_macro(name, CONFIG)
    assert result.kind == "macro" and result.expression == name
    assert result.reason is reason


def test_explicitly_undefined_macro_has_no_value_but_evaluates_as_zero_in_an_expression():
    config = MacroConfiguration(undefined=["OFF"])
    macro = evaluate_macro("OFF", config)
    assert macro.reason is EvaluationReason.UNRESOLVED_IDENTIFIER
    assert "not defined" in macro.message
    assert evaluate_expression("OFF + 1", config).value == 1


def test_unknown_name_policy_follows_the_configuration():
    open_world = MacroConfiguration()
    closed_world = MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED)
    assert evaluate_expression("MISSING + 1", open_world).concrete is False
    assert evaluate_expression("MISSING + 1", closed_world).value == 1


def test_self_referential_macro_follows_if_semantics():
    assert evaluate_macro("SELF", CONFIG).value == 1


def test_preprocessing_context_supplies_standard_macros():
    context = PreprocessingContext(standard_macros={"__STDC_HOSTED__": "1"})
    assert evaluate_expression("__STDC_HOSTED__ + 1", context=context).value == 2
    conflict = MacroConfiguration(integers={"__STDC_HOSTED__": 0})
    with pytest.raises(AnalysisError) as raised:
        evaluate_expression("1", conflict, context=context)
    assert raised.value.code is ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "call",
    [
        lambda: evaluate_expression(None),
        lambda: evaluate_expression("1", configuration="CONFIG"),
        lambda: evaluate_expression("1", context="context"),
        lambda: evaluate_expression("1", options="options"),
        lambda: evaluate_macro(None, CONFIG),
        lambda: evaluate_macro("not an identifier", CONFIG),
        lambda: evaluate_macro("1A", CONFIG),
        lambda: evaluate_macro("AREA", None),
        lambda: evaluate_macro("AREA", CONFIG, options="options"),
    ],
)
def test_invalid_arguments_raise_analysis_error(call):
    with pytest.raises(AnalysisError):
        call()


def test_offsets_locate_the_token_or_its_macro():
    assert evaluate_expression("1 + UNKNOWN", CONFIG).offset == 4
    assert evaluate_expression("1 2", CONFIG).offset == 2
    # A token that comes from a replacement list is located at the macro that produced it.
    config = MacroConfiguration(definitions=[MacroDefinition("HALF", "(1 / 0)")])
    assert evaluate_expression("2 + HALF", config).offset == 4


def test_evaluation_is_immutable_and_hashable():
    result = evaluate_macro("AREA", CONFIG)
    with pytest.raises(AttributeError):
        result.value = 1  # type: ignore[misc]
    assert hash(result) == hash(evaluate_macro("AREA", CONFIG))


def test_to_dict_has_every_field_and_a_stable_order():
    concrete = evaluate_macro("AREA", CONFIG).to_dict()
    assert list(concrete) == [
        "kind",
        "expression",
        "state",
        "value",
        "unsigned",
        "expanded",
        "reason",
        "message",
        "offset",
    ]
    assert concrete == {
        "kind": "macro",
        "expression": "AREA",
        "state": "concrete",
        "value": 14,
        "unsigned": False,
        "expanded": "(3 * 4 + 2)",
        "reason": None,
        "message": None,
        "offset": None,
    }
    failed = evaluate_expression("1 + UNKNOWN").to_dict()
    assert failed["state"] == "unevaluable"
    assert failed["reason"] == "unresolved_identifier"
    assert failed["value"] is None and failed["unsigned"] is None
    assert failed["offset"] == 4


def test_every_reason_has_a_distinct_stable_value():
    assert [reason.value for reason in EvaluationReason] == [
        "division_by_zero",
        "overflow",
        "unresolved_identifier",
        "unsupported_syntax",
        "implementation_defined",
        "function_like",
        "nesting_bound",
        "expansion_error",
        "budget_exceeded",
    ]


def test_document_carries_the_schema_first():
    document = evaluation_document(evaluate_macro("AREA", CONFIG))
    assert list(document)[0] == "schema"
    assert document["schema"] == "cpre.evaluation/1"
    assert document["value"] == 14


def test_results_are_deterministic():
    runs = [evaluate_expression("SQUARE(AREA) + SUM", CONFIG).to_dict() for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


CONDITIONS = [
    "1 + 2 * 3 == 7",
    "AREA > 13",
    "AREA % 5 == 4",
    "SUM * 2 == 11",
    "NEGATIVE < 0",
    "-1 > 0u",
    "1 ? 2 : 0",
    "0 && (1 / 0)",
    "WIDTH - HEIGHT",
    "SQUARE(PAD) - 4",
]


@pytest.mark.parametrize("condition", CONDITIONS)
def test_value_agrees_with_the_branch_concrete_preprocessing_selects(condition):
    definitions = (
        "".join(
            f"#define {d.name} {d.replacement}\n"
            for d in CONFIG.definitions
            if d.parameters is None and d.name != "SELF"
        )
        + "#define SQUARE(x) ((x) * (x))\n"
    )
    source = f"{definitions}#if {condition}\ntrue_branch\n#else\nfalse_branch\n#endif\n"
    result = preprocess_source(source)
    assert result.complete
    evaluated = evaluate_expression(condition, CONFIG)
    assert evaluated.concrete
    assert (evaluated.value != 0) is ("true_branch" in result.source)


ALPHABET = list("0123456789+-*/%()<>=!&|^~?:xXuUlL ,'\"\\#.eAB_") + ["WIDTH", "SQUARE(", "defined"]


@settings(
    derandomize=True,
    max_examples=int(os.environ.get("CPRE_PROPERTY_EXAMPLES", "300")),
    deadline=None,
)
@example("1" + "(" * 200)
@example("9" * 5000)
@example("SQUARE(" * 50)
@given(st.lists(st.sampled_from(ALPHABET), max_size=24).map("".join))
def test_arbitrary_text_never_raises_and_always_explains(text):
    result = evaluate_expression(text, CONFIG, options=AnalysisOptions(max_work=20_000))
    if result.concrete:
        assert isinstance(result.value, int) and result.reason is None
    else:
        assert result.reason is not None and result.message


@pytest.mark.parametrize(
    "call",
    [
        lambda: evaluate_expression(1),
        lambda: evaluate_macro(None, CONFIG),
        lambda: evaluate_macro("not an identifier", CONFIG),
    ],
)
def test_invalid_text_uses_the_invalid_configuration_code(call):
    with pytest.raises(AnalysisError) as raised:
        call()
    assert raised.value.code is ErrorCode.INVALID_CONFIGURATION


def test_invalid_options_keep_the_analysis_failure_code():
    with pytest.raises(AnalysisError) as raised:
        evaluate_expression("1", options="not options")
    assert raised.value.code is ErrorCode.ANALYSIS_FAILURE
