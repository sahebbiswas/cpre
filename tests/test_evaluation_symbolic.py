"""A name the configuration does not settle gives a symbolic result, never a guess (#221)."""

from __future__ import annotations

import json

import pytest

import cpre
from cpre import (
    EvaluationReason,
    EvaluationState,
    MacroConfiguration,
    MacroDefinition,
    UnknownNamePolicy,
    evaluate_expression,
    evaluate_macro,
)
from cpre.cli import main
from cpre.serialization import evaluation_document

CONFIG = MacroConfiguration(
    integers={"WIDTH": 3},
    definitions=[
        MacroDefinition("AREA", "(WIDTH * 2 + PAD)"),
        MacroDefinition("SUM", "WIDTH + BASE"),
        MacroDefinition("SQUARE", "((x) * (x))", ("x",)),
        MacroDefinition("BIG", "9223372036854775807"),
    ],
)


def symbolic(text: str, config: MacroConfiguration = CONFIG):
    result = evaluate_expression(text, config)
    assert result.state is EvaluationState.SYMBOLIC, result
    assert result.symbolic and not result.concrete
    assert result.value is None and result.unsigned is None
    assert result.reason is EvaluationReason.UNRESOLVED_IDENTIFIER and result.message
    return result


def test_one_unresolved_name_keeps_the_settled_parts_substituted():
    result = symbolic("BASE + 4 * 2")
    assert (result.expanded, result.unresolved, result.offset) == ("BASE + 4 * 2", ("BASE",), 0)
    result = symbolic("WIDTH * 2 + PAD")
    assert (result.expanded, result.unresolved) == ("3 * 2 + PAD", ("PAD",))
    assert result.offset == 12


def test_a_macro_replacement_with_an_unresolved_name_is_symbolic():
    result = evaluate_macro("AREA", CONFIG)
    assert result.state is EvaluationState.SYMBOLIC
    assert (result.expanded, result.unresolved) == ("(3 * 2 + PAD)", ("PAD",))
    assert symbolic("AREA % 5").expanded == "(3 * 2 + PAD) % 5"


def test_several_unresolved_names_are_listed_once_in_order():
    result = symbolic("B + A * B + SUM")
    assert result.unresolved == ("B", "A", "BASE")
    assert result.expanded == "B + A * B + 3 + BASE"


def test_use_site_precedence_is_preserved_not_folded():
    # The structure is exactly what the compiler sees: no regrouping, no folding.
    result = symbolic("SUM * 2")
    assert result.expanded == "3 + BASE * 2"


@pytest.mark.parametrize(
    ("text", "names", "expanded"),
    [
        ("F(1)", ("F",), "F(1)"),
        ("F(1, 2) + 1", ("F",), "F(1, 2) + 1"),
        ("F(WIDTH, G(PAD))", ("F", "G", "PAD"), "F(3, G(PAD))"),
        ("1 + F()", ("F",), "1 + F()"),
    ],
)
def test_a_call_of_an_unconfigured_function_like_name_is_symbolic(text, names, expanded):
    result = symbolic(text)
    assert result.unresolved == names and result.expanded == expanded


def test_an_unbalanced_call_is_unsupported_not_symbolic():
    result = evaluate_expression("F(1", CONFIG)
    assert result.state is EvaluationState.UNEVALUABLE
    assert result.reason is EvaluationReason.UNSUPPORTED_SYNTAX


@pytest.mark.parametrize(
    ("text", "names", "expanded"),
    [
        ("defined(MISSING) + 1", ("MISSING",), "defined(MISSING) + 1"),
        ("defined MISSING && WIDTH", ("MISSING",), "defined MISSING && 3"),
    ],
)
def test_unknown_definedness_is_an_unresolved_name(text, names, expanded):
    result = symbolic(text)
    assert result.unresolved == names and result.expanded == expanded


@pytest.mark.parametrize(
    "text",
    ["-UNKNOWN", "!UNKNOWN", "~UNKNOWN * 2", "UNKNOWN ? 1 : 2", "1 ? UNKNOWN : 2", "(UNKNOWN)"],
)
def test_unresolved_names_pass_through_operators(text):
    assert symbolic(text).unresolved == ("UNKNOWN",)


@pytest.mark.parametrize("text", ["0 && UNKNOWN", "1 || UNKNOWN", "1 ? 2 : UNKNOWN"])
def test_an_unresolved_name_in_an_unevaluated_operand_stays_symbolic(text):
    # Whether such a result is determinate is tracked in #224; nothing is guessed meanwhile.
    result = symbolic(text)
    assert result.unresolved == ("UNKNOWN",)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("UNKNOWN + 1 / 0", EvaluationReason.DIVISION_BY_ZERO),
        ("1 / 0 + UNKNOWN", EvaluationReason.DIVISION_BY_ZERO),
        ("UNKNOWN % 0", EvaluationReason.DIVISION_BY_ZERO),
        ("UNKNOWN / (WIDTH - 3)", EvaluationReason.DIVISION_BY_ZERO),
        ("UNKNOWN + BIG + 1", None),  # depends on UNKNOWN: symbolic
        ("BIG + 1 + UNKNOWN", EvaluationReason.OVERFLOW),
        ("UNKNOWN << 64", EvaluationReason.OVERFLOW),
        ("UNKNOWN << -1", EvaluationReason.OVERFLOW),
        ("UNKNOWN + 1.5", EvaluationReason.UNSUPPORTED_SYNTAX),
        ("UNKNOWN + 'a'", EvaluationReason.IMPLEMENTATION_DEFINED),
        ("'ab' && UNKNOWN", EvaluationReason.IMPLEMENTATION_DEFINED),
        ("(UNKNOWN, 1)", EvaluationReason.UNSUPPORTED_SYNTAX),
        ("UNKNOWN + " + "(" * 40 + "1" + ")" * 40, EvaluationReason.NESTING_BOUND),
    ],
)
def test_another_obstacle_makes_the_result_unevaluable(text, reason):
    result = evaluate_expression(text, CONFIG)
    if reason is None:
        assert result.state is EvaluationState.SYMBOLIC
        return
    assert result.state is EvaluationState.UNEVALUABLE
    assert result.reason is reason
    assert result.unresolved == ()


def test_a_division_by_zero_that_depends_on_the_unresolved_name_stays_symbolic():
    # `1 / UNKNOWN` fails only if UNKNOWN is 0, and `UNKNOWN && 1 / 0` only if it is not.
    for text in ("1 / UNKNOWN", "UNKNOWN && 1 / 0", "UNKNOWN ? 1 / 0 : 2", "UNKNOWN || 1 % 0"):
        assert symbolic(text).unresolved == ("UNKNOWN",)


def test_the_offset_points_at_the_blocking_token():
    assert evaluate_expression("UNKNOWN + 1 / 0", CONFIG).offset == 14
    assert evaluate_expression("1 + UNKNOWN", CONFIG).offset == 4


def test_unknown_names_policy_decides_what_is_unresolved():
    undefined = MacroConfiguration(integers={"WIDTH": 3}, unknown_names=UnknownNamePolicy.UNDEFINED)
    result = evaluate_expression("WIDTH + OTHER", undefined)
    assert result.concrete and result.value == 3
    assert evaluate_expression("OTHER + 1").symbolic
    explicit = MacroConfiguration(undefined=["OFF"])
    assert evaluate_expression("OFF + 1", explicit).value == 1
    assert evaluate_expression("OFF + ON", explicit).symbolic


def test_a_macro_that_is_not_settled_is_symbolic_but_one_that_is_undefined_is_not():
    unsettled = evaluate_macro("MYSTERY", MacroConfiguration())
    assert unsettled.state is EvaluationState.SYMBOLIC
    assert (unsettled.expanded, unsettled.unresolved) == ("MYSTERY", ("MYSTERY",))
    absent = evaluate_macro("OFF", MacroConfiguration(undefined=["OFF"]))
    assert absent.state is EvaluationState.UNEVALUABLE
    assert absent.reason is EvaluationReason.UNRESOLVED_IDENTIFIER


def test_a_settled_expression_is_still_concrete():
    result = evaluate_expression("WIDTH * 2", CONFIG)
    assert result.concrete and result.value == 6 and result.unresolved == ()


def test_the_record_carries_the_state_and_names():
    record = evaluation_document(evaluate_expression("AREA % 5", CONFIG))
    assert record["schema"] == "cpre.evaluation/1"
    assert record["state"] == "symbolic"
    assert record["unresolved"] == ["PAD"] and record["expanded"] == "(3 * 2 + PAD) % 5"
    assert record["value"] is None and record["reason"] == "unresolved_identifier"


def test_symbolic_results_are_deterministic_and_bounded():
    text = " + ".join(f"N{i}" for i in range(200))
    first, second = evaluate_expression(text), evaluate_expression(text)
    assert first == second and len(first.unresolved) == 200
    tiny = evaluate_expression(text, options=cpre.AnalysisOptions(max_work=20))
    assert tiny.state is EvaluationState.UNEVALUABLE
    assert tiny.reason is EvaluationReason.BUDGET_EXCEEDED


def _cli(capsys, *argv):
    code = main(["evaluate", *argv])
    return code, capsys.readouterr().out


def test_cli_prints_the_symbolic_expression_and_the_names(capsys):
    code, out = _cli(
        capsys, "-D", "WIDTH=3", "-D", "AREA=(WIDTH * 2 + PAD)", "AREA % 5", "--color", "never"
    )
    assert code == 3
    assert out.splitlines() == [
        "- AREA % 5",
        "~ (3 * 2 + PAD) % 5",
        "  unresolved  PAD",
    ]


def test_cli_exit_statuses_order_unevaluable_over_symbolic_over_concrete(capsys):
    assert _cli(capsys, "1 + 2")[0] == 0
    assert _cli(capsys, "1 + 2", "UNKNOWN + 1")[0] == 3
    assert _cli(capsys, "UNKNOWN + 1", "1 / 0")[0] == 1
    assert _cli(capsys, "1 / 0", "UNKNOWN + 1", "1 + 2")[0] == 1


def test_cli_json_holds_the_state_and_names(capsys):
    code, out = _cli(capsys, "-D", "W=3", "W + BASE", "W * 2", "--json")
    first, second = json.loads(out)["evaluations"]
    assert code == 3
    assert (first["state"], first["unresolved"], first["expanded"]) == (
        "symbolic",
        ["BASE"],
        "3 + BASE",
    )
    assert (second["state"], second["value"], second["unresolved"]) == ("concrete", 6, [])


def test_cli_unevaluable_item_still_shows_its_reason(capsys):
    code, out = _cli(capsys, "BASE + 1 / 0", "--color", "never")
    assert code == 1
    assert out.splitlines()[-1].startswith("! division_by_zero:")


def test_cli_color_only_adds_escape_sequences(capsys):
    _, plain = _cli(capsys, "BASE + 1", "--color", "never")
    _, colored = _cli(capsys, "BASE + 1", "--color", "always")
    import re

    assert "\x1b[" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain


@pytest.mark.parametrize(
    "text",
    [
        "sizeof(int)",
        "sizeof(int) + UNKNOWN",
        "UNKNOWN + sizeof x",
        "_Alignof(long) * 2",
        "alignof(int) + UNKNOWN",
        "UNKNOWN + int",
        "unsigned + UNKNOWN",
        "_Generic(1, int: 2) + UNKNOWN",
        "UNKNOWN + struct",
        "UNKNOWN + enum",
        "F(sizeof(int))",  # a keyword inside the arguments of an unconfigured call
        "F(1, unsigned) + 1",
        "UNKNOWN + union",
    ],
)
def test_c_keywords_are_not_unconfigured_macros(text):
    result = evaluate_expression(text, CONFIG)
    assert result.state is EvaluationState.UNEVALUABLE, result
    assert result.reason is EvaluationReason.UNSUPPORTED_SYNTAX
    assert result.unresolved == ()
