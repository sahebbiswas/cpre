"""``trace=True`` and ``cpre evaluate --explain`` show how a value was derived (#222)."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import cpre
from cpre import (
    EvaluationState,
    MacroConfiguration,
    MacroDefinition,
    Trace,
    TraceKind,
    TraceStep,
    evaluate_expression,
    evaluate_macro,
)
from cpre._evaluation_types import TRACE_LIMIT
from cpre.cli import main

CONFIG = MacroConfiguration(
    definitions=[
        MacroDefinition("W", "3"),
        MacroDefinition("H", "4"),
        MacroDefinition("SUM", "W + H"),
        MacroDefinition("AREA", "(W * H + PAD)"),
        MacroDefinition("SAFE", "(W + H)"),
        MacroDefinition("DIFF", "H - W"),
        MacroDefinition("PRODUCT", "W * H"),
        MacroDefinition("CHAIN", "SAFE"),
        MacroDefinition("SQ", "((x) * (x))", ("x",)),
        MacroDefinition("SQ_RAW", "x * x", ("x",)),
        MacroDefinition("PICK", "W > 2 ? H : W"),
    ]
)


def steps(text: str, config: MacroConfiguration = CONFIG) -> list[TraceStep]:
    result = evaluate_expression(text, config, trace=True)
    assert result.trace is not None
    return list(result.trace.steps)


def kinds(items: list[TraceStep]) -> list[str]:
    return [step.kind.value for step in items]


def of(kind: TraceKind, items: list[TraceStep]) -> list[TraceStep]:
    return [step for step in items if step.kind is kind]


def test_a_trace_is_only_present_when_requested():
    plain = evaluate_expression("SUM * 2", CONFIG)
    assert plain.trace is None and "trace" not in plain.to_dict()
    traced = evaluate_expression("SUM * 2", CONFIG, trace=True)
    assert isinstance(traced.trace, Trace) and "trace" in traced.to_dict()


def test_a_nested_macro_chain_shows_each_expansion_in_order():
    items = steps("CHAIN * 2")
    assert kinds(items)[0] == "start" and items[0].text == "CHAIN * 2"
    expansions = [(step.text, step.replacement) for step in of(TraceKind.EXPAND, items)]
    assert expansions == [
        ("CHAIN", "SAFE"),
        ("SAFE", "(W + H)"),
        ("W", "3"),
        ("H", "4"),
    ]
    expanded = of(TraceKind.EXPANDED, items)
    assert [step.text for step in expanded] == ["(3 + 4) * 2"]
    assert kinds(items).index("expanded") > max(
        index for index, kind in enumerate(kinds(items)) if kind == "expand"
    )


def test_the_operations_are_evaluated_inside_out_with_results():
    items = of(TraceKind.EVALUATE, steps("(1 + 2) * 3 - 4"))
    assert [(step.text, step.value, step.state) for step in items] == [
        ("1 + 2", 3, EvaluationState.CONCRETE),
        ("(1 + 2) * 3", 9, EvaluationState.CONCRETE),
        ("(1 + 2) * 3 - 4", 5, EvaluationState.CONCRETE),
    ]
    assert all(step.unsigned is False for step in items)


def test_the_last_step_is_the_result():
    last = steps("SUM * 2")[-1]
    assert (last.kind, last.state, last.value, last.unsigned) == (
        TraceKind.RESULT,
        EvaluationState.CONCRETE,
        11,
        False,
    )


def test_use_site_precedence_is_warned_about():
    items = steps("SUM * 2")
    (warning,) = of(TraceKind.WARNING, items)
    assert warning.text == "SUM" and warning.replacement == "W + H"
    assert warning.note is not None and "'+'" in warning.note and "'*'" in warning.note
    assert kinds(items).index("warning") == kinds(items).index("expand") + 1
    # The expansion that follows shows the regrouping the warning is about.
    assert of(TraceKind.EXPANDED, items)[0].text == "3 + 4 * 2"


@pytest.mark.parametrize(
    "text",
    [
        "SAFE * 2",  # parenthesized replacement
        "SUM",  # nothing around it
        "1 + SUM",  # equal precedence on the right of an associative operator
        "SUM + 1",
        "SUM - 1",  # left operand of a left-associative operator
        "SUM << 1",  # a looser operator next to it
        "1 + PRODUCT",  # a tighter replacement next to a looser operator
        "W * 2",  # no operator in the replacement
        "SQ(W + 1)",  # parenthesized
    ],
)
def test_no_warning_when_the_use_groups_the_way_it_reads(text):
    assert of(TraceKind.WARNING, steps(text)) == []


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("2 * SUM", "SUM"),
        ("-SUM", "SUM"),
        ("!SUM", "SUM"),
        ("~SUM", "SUM"),
        ("10 - DIFF", "DIFF"),  # right operand of a non-associative operator
        ("PICK + 1", "PICK"),  # an unparenthesized conditional
        ("SUM % 4", "SUM"),
    ],
)
def test_a_warning_for_each_kind_of_regrouping(text, name):
    (warning, *rest) = of(TraceKind.WARNING, steps(text))
    assert warning.text == name and rest == []


def test_a_signed_operand_becoming_unsigned_is_flagged():
    items = steps("-1 < 0u")
    (conversion,) = of(TraceKind.CONVERSION, items)
    assert conversion.text == "-1 < 0u"
    assert conversion.note == "signed -1 becomes unsigned 18446744073709551615"
    comparison = of(TraceKind.EVALUATE, items)[-1]
    assert (comparison.value, comparison.unsigned) == (0, False)
    assert kinds(items).index("conversion") < kinds(items).index("evaluate", 3)


def test_a_conversion_that_keeps_the_value_says_so():
    (conversion,) = of(TraceKind.CONVERSION, steps("5 + 1u"))
    assert conversion.note == "signed 5 becomes unsigned (the value is unchanged)"


@pytest.mark.parametrize(
    "text",
    ["1 + 2", "1u + 2u", "-1 >> 1u", "1u << 3", "1 && 2u", "1 || 0u", "!1u"],
)
def test_no_conversion_flag_without_a_signed_to_unsigned_step(text):
    assert of(TraceKind.CONVERSION, steps(text)) == []


def test_a_conditional_converts_its_signed_arm():
    items = steps("1 ? -1 : 0u")
    (conversion,) = of(TraceKind.CONVERSION, items)
    assert conversion.note == "signed -1 becomes unsigned 18446744073709551615"
    assert of(TraceKind.EVALUATE, items)[-1].unsigned is True
    assert of(TraceKind.CONVERSION, steps("0 ? -1 : 0u")) == []  # the unsigned arm is chosen


def test_a_function_like_call_shows_its_arguments_and_replacement():
    items = steps("SQ(W + 1) - 1")
    expansions = [(step.text, step.replacement) for step in of(TraceKind.EXPAND, items)]
    assert expansions == [("W", "3"), ("SQ(W + 1)", "((3 + 1) * (3 + 1))")]
    assert of(TraceKind.EXPANDED, items)[0].text == "((3 + 1) * (3 + 1)) - 1"
    assert of(TraceKind.EVALUATE, items)[-1].value == 15


def test_an_unparenthesized_function_like_body_shows_the_regrouping():
    items = steps("SQ_RAW(W + 1)")
    assert of(TraceKind.EXPANDED, items)[0].text == "3 + 1 * 3 + 1"
    assert of(TraceKind.RESULT, items)[0].value == 7


def test_a_symbolic_result_shows_symbolic_operations_and_the_unresolved_names():
    items = steps("AREA % 5")
    evaluations = [(step.text, step.state, step.value) for step in of(TraceKind.EVALUATE, items)]
    assert evaluations == [
        ("3 * 4", EvaluationState.CONCRETE, 12),
        ("3 * 4 + PAD", EvaluationState.SYMBOLIC, None),
        ("(3 * 4 + PAD) % 5", EvaluationState.SYMBOLIC, None),
    ]
    stop = items[-1]
    assert stop.kind is TraceKind.STOP and stop.state is EvaluationState.SYMBOLIC
    assert stop.replacement == "(3 * 4 + PAD) % 5"
    assert stop.note is not None and "PAD" in stop.note


def test_an_unevaluable_result_shows_where_evaluation_stopped():
    items = steps("1 + 2 / 0")
    stop = items[-1]
    assert stop.kind is TraceKind.STOP and stop.state is EvaluationState.UNEVALUABLE
    assert stop.note is not None
    assert stop.note.startswith("division_by_zero:") and "(offset 8)" in stop.note
    assert of(TraceKind.EVALUATE, items) == []  # nothing completed before the failure


def test_work_before_the_stop_is_kept():
    items = steps("(1 + 2) * 3 + 1 / 0")
    assert [step.text for step in of(TraceKind.EVALUATE, items)] == ["1 + 2", "(1 + 2) * 3"]
    assert items[-1].kind is TraceKind.STOP


def test_an_expansion_error_and_an_exhausted_budget_end_in_a_stop():
    broken = evaluate_expression("SQ(1, 2)", CONFIG, trace=True)
    assert broken.trace is not None and broken.trace.steps[-1].kind is TraceKind.STOP
    tiny = evaluate_expression(
        "1 + 1 + 1 + 1 + 1", options=cpre.AnalysisOptions(max_work=5), trace=True
    )
    assert tiny.trace is not None
    assert tiny.trace.steps[-1].note is not None
    assert tiny.trace.steps[-1].note.startswith("budget_exceeded:")


def test_short_circuited_operands_are_not_evaluated():
    items = of(TraceKind.EVALUATE, steps("0 && 1 / 0"))
    assert [(step.text, step.value) for step in items] == [("0 && 1 / 0", 0)]


def test_a_macro_trace_starts_from_the_name():
    result = evaluate_macro(
        "AREA",
        MacroConfiguration(
            integers={"W": 3, "H": 4, "PAD": 2},
            definitions=[MacroDefinition("AREA", "(W * H + PAD)")],
        ),
        trace=True,
    )
    assert result.trace is not None
    assert result.trace.steps[0] == TraceStep(TraceKind.START, "AREA")
    assert result.trace.steps[-1].kind is TraceKind.RESULT and result.value == 14
    assert [s.text for s in result.trace.steps if s.kind is TraceKind.EXPAND][0] == "AREA"


@pytest.mark.parametrize(
    ("name", "config"),
    [
        ("SQ", CONFIG),  # function-like
        ("MISSING", MacroConfiguration(undefined=["MISSING"])),  # not defined
        ("MYSTERY", MacroConfiguration()),  # not settled
    ],
)
def test_macros_that_are_not_evaluated_still_get_a_trace(name, config):
    result = evaluate_macro(name, config, trace=True)
    assert result.trace is not None
    assert [step.kind for step in result.trace.steps] == [TraceKind.START, TraceKind.STOP]


def test_each_step_has_every_field_in_a_stable_order():
    record = evaluate_expression("SUM * 2", CONFIG, trace=True).to_dict()
    trace = record["trace"]
    assert isinstance(trace, dict) and list(trace) == ["truncated", "steps"]
    assert trace["truncated"] is False
    first = trace["steps"][0]
    assert list(first) == ["kind", "text", "replacement", "state", "value", "unsigned", "note"]
    assert first == {
        "kind": "start",
        "text": "SUM * 2",
        "replacement": None,
        "state": None,
        "value": None,
        "unsigned": None,
        "note": None,
    }
    json.dumps(record)  # serializable as is


def test_the_trace_is_bounded_and_truncation_is_reported():
    text = " + ".join(["1"] * (TRACE_LIMIT + 50))
    result = evaluate_expression(text, trace=True)
    assert result.trace is not None
    assert result.trace.truncated
    assert len(result.trace.steps) == TRACE_LIMIT + 1  # the outcome is always kept
    last = result.trace.steps[-1]
    assert last.kind is TraceKind.RESULT and last.value == TRACE_LIMIT + 50
    assert result.value == TRACE_LIMIT + 50


def test_a_short_trace_is_not_marked_truncated():
    result = evaluate_expression("1 + 2", trace=True)
    assert result.trace is not None and not result.trace.truncated


def test_the_trace_is_deterministic():
    first = evaluate_expression("SUM * 2 + AREA", CONFIG, trace=True)
    second = evaluate_expression("SUM * 2 + AREA", CONFIG, trace=True)
    assert first == second and first.trace == second.trace


@pytest.mark.parametrize(
    "text",
    [
        "SUM * 2",
        "AREA % 5",
        "1 / 0",
        "-1 < 0u",
        "SQ(W) + UNKNOWN",
        "9223372036854775807 + 1",
        "(1,",
        "'ab'",
        "1 ? 2 : 3 / 0",
        "PICK",
        "",
    ],
)
def test_tracing_never_changes_the_result(text):
    plain = evaluate_expression(text, CONFIG)
    traced = evaluate_expression(text, CONFIG, trace=True)
    assert replace(traced, trace=None) == plain
    assert traced.trace is not None and traced.trace.steps[0].kind is TraceKind.START
    assert traced.trace.steps[-1].kind in {TraceKind.RESULT, TraceKind.STOP}


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.sampled_from(
            [
                "1",
                "2u",
                "W",
                "H",
                "SUM",
                "UNK",
                "(",
                ")",
                "+",
                "-",
                "*",
                "/",
                "%",
                "<<",
                "&&",
                "||",
                "?",
                ":",
                "!",
                "~",
                "==",
                "<",
                "SQ(",
                "0",
                "-1",
                "'a'",
                ",",
            ]
        ),
        max_size=14,
    )
)
def test_tracing_is_equivalent_for_arbitrary_input(parts):
    text = " ".join(parts)
    plain = evaluate_expression(text, CONFIG)
    traced = evaluate_expression(text, CONFIG, trace=True)
    assert replace(traced, trace=None) == plain
    assert traced.trace is not None
    assert traced.trace.steps[-1].kind in {TraceKind.RESULT, TraceKind.STOP}


def test_the_step_kinds_have_stable_values():
    assert [kind.value for kind in TraceKind] == [
        "start", "expand", "expanded", "warning", "conversion", "evaluate", "result", "stop",
    ]  # fmt: skip


# --- cpre evaluate --explain ---------------------------------------------------------------

DEFINES = ["-D", "W=3", "-D", "H=4", "-D", "SUM=W + H", "-D", "AREA=(W * H + PAD)"]


def run(capsys, *argv):
    code = main(["evaluate", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_explain_prints_the_derivation_under_the_value(capsys):
    code, out, err = run(capsys, *DEFINES, "SUM * 2", "--explain", "--color", "never")
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[:4] == ["- SUM * 2", "+ 11", "  expanded  3 + 4 * 2", "  trace"]
    body = lines[4:]
    assert body[0] == "    start     SUM * 2"
    assert body[1] == "    expand    SUM → W + H"
    assert body[2].startswith("    warning   SUM: the top-level '+' in SUM's replacement")
    assert body[-3:] == [
        "    evaluate  4 * 2 = 8",
        "    evaluate  3 + 4 * 2 = 11",
        "    result    SUM * 2 = 11",
    ]


def test_explain_shows_a_conversion_and_an_unsigned_result(capsys):
    _, out, _ = run(capsys, "-1 < 0u", "0u - 1", "--explain", "--color", "never")
    assert "    conversion  -1 < 0u  (signed -1 becomes unsigned 18446744073709551615)" in out
    assert "    result      0u - 1 = 18446744073709551615 (unsigned)" in out


def test_explain_shows_symbolic_and_unevaluable_items(capsys):
    code, out, _ = run(capsys, *DEFINES, "AREA", "1 + 2 / 0", "--explain", "--color", "never")
    assert code == 1
    assert "    evaluate  3 * 4 + PAD = symbolic" in out
    assert "    stop      no value while these names are unresolved: PAD" in out
    assert (
        "    stop      division_by_zero: division by zero in concrete condition (offset 8)" in out
    )


def test_explain_keeps_the_exit_status_rules(capsys):
    assert run(capsys, "1 + 2", "--explain")[0] == 0
    assert run(capsys, "UNK + 1", "--explain")[0] == 3
    assert run(capsys, "1 / 0", "--explain")[0] == 1


def test_explain_with_json_adds_the_trace_to_each_record(capsys):
    code, out, _ = run(capsys, *DEFINES, "SUM * 2", "W", "--explain", "--json")
    first, second = json.loads(out)["evaluations"]
    assert code == 0
    assert first["trace"]["truncated"] is False
    assert [s["kind"] for s in first["trace"]["steps"]][:3] == ["start", "expand", "warning"]
    assert second["trace"]["steps"][-1]["kind"] == "result"


def test_json_without_explain_has_no_trace(capsys):
    _, out, _ = run(capsys, *DEFINES, "SUM * 2", "--json")
    assert "trace" not in json.loads(out)["evaluations"][0]


def test_explain_color_only_adds_escape_sequences(capsys):
    _, plain, _ = run(capsys, *DEFINES, "SUM * 2", "-1 < 0u", "--explain", "--color", "never")
    _, colored, _ = run(capsys, *DEFINES, "SUM * 2", "-1 < 0u", "--explain", "--color", "always")
    assert "\x1b[" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain


def test_explain_reports_truncation(capsys):
    text = " + ".join(["1"] * (TRACE_LIMIT + 10))
    code, out, _ = run(capsys, text, "--explain", "--color", "never")
    assert code == 0
    lines = out.splitlines()
    assert lines[-1].startswith("    truncated")
    assert lines[-2].startswith("    result") and f"= {TRACE_LIMIT + 10}" in lines[-2]


def test_explain_shows_control_characters_as_escapes(capsys):
    _, out, _ = run(
        capsys, "-D", "HOSTILE=1\x1b]0;x\x07", "HOSTILE + 1", "--explain", "--color", "never"
    )
    assert "\x1b" not in out and "\x07" not in out
    assert "\\x1b" in out and "\\x07" in out
