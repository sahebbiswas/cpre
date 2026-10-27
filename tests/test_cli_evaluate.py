"""``cpre evaluate`` prints and serializes macro and expression values."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from cpre.cli import main

DEFINES = ["-D", "WIDTH=3", "-D", "HEIGHT=4", "-D", "PAD=2", "-D", "AREA=(WIDTH * HEIGHT + PAD)"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _evaluate(capsys, *argv):
    code = main(["evaluate", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_macro_and_expression_values_with_their_expansion(capsys):
    code, out, err = _evaluate(
        capsys, *DEFINES, "-D", "SUM=WIDTH + HEIGHT", "AREA", "SUM * 2", "--color", "never"
    )
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "- AREA",
        "+ 14",
        "  expanded  (3 * 4 + 2)",
        "- SUM * 2",
        "+ 11",
        "  expanded  3 + 4 * 2",
    ]


def test_expanded_line_is_omitted_when_expansion_changed_nothing(capsys):
    _, out, _ = _evaluate(capsys, "1 + 2 * 3", "--color", "never")
    assert out.splitlines() == ["- 1 + 2 * 3", "+ 7"]


def test_unsigned_values_are_marked(capsys):
    _, out, _ = _evaluate(capsys, "0u - 1", "--color", "never")
    assert out.splitlines()[1] == "+ 18446744073709551615  (unsigned)"


def test_negative_operands_follow_a_double_dash(capsys):
    code, out, _ = _evaluate(capsys, "--color", "never", "--", "-7 / 2", "-7 % 3")
    assert code == 0
    assert out.splitlines() == ["- -7 / 2", "+ -3", "- -7 % 3", "+ -1"]


def test_a_bare_identifier_is_a_macro_and_anything_else_an_expression(capsys):
    _, out, _ = _evaluate(capsys, *DEFINES, "  WIDTH  ", "(WIDTH)", "--json")
    records = json.loads(out)["evaluations"]
    assert [r["kind"] for r in records] == ["macro", "expression"]
    assert [r["expression"] for r in records] == ["WIDTH", "(WIDTH)"]
    assert [r["value"] for r in records] == [3, 3]


def test_an_item_without_a_value_exits_one_and_shows_why(capsys):
    code, out, err = _evaluate(capsys, *DEFINES, "AREA", "1 + UNKNOWN", "--color", "never")
    assert (code, err) == (1, "")
    lines = out.splitlines()
    assert lines[:3] == ["- AREA", "+ 14", "  expanded  (3 * 4 + 2)"]
    assert lines[3:] == [
        "- 1 + UNKNOWN",
        "      ^",
        "! unresolved_identifier: unresolved identifier 'UNKNOWN'",
    ]


def test_a_macro_failure_has_a_caret_under_the_macro(capsys):
    code, out, _ = _evaluate(
        capsys, "-D", "ZERO=0", "-D", "BAD=1 / ZERO", "BAD", "--color", "never"
    )
    assert code == 1
    assert out.splitlines()[:3] == ["- BAD", "  ^", out.splitlines()[2]]
    assert out.splitlines()[2].startswith("! division_by_zero")


def test_control_characters_are_shown_as_escapes_in_text_but_not_in_json(capsys):
    hostile = "1 +\x1b]0;title\x07\n2 / 0"
    code, out, _ = _evaluate(capsys, hostile, "--color", "never")
    assert code == 1
    assert "\x1b" not in out and "\x07" not in out
    assert "\\x1b]0;title\\x07\\x0a2 / 0" in out
    assert "^" not in out  # the operand is not shown as given, so no caret points into it
    _, out, _ = _evaluate(capsys, hostile, "--json")
    assert json.loads(out)["evaluations"][0]["expression"] == hostile


@pytest.mark.parametrize(
    "item, reason",
    [
        ("AREA / (PAD - 2)", "division_by_zero"),
        ("9223372036854775807 + 1", "overflow"),
        ("'ab'", "implementation_defined"),
        ("1 +", "unsupported_syntax"),
        ("NOPE", "unresolved_identifier"),
    ],
)
def test_each_reason_is_reported(capsys, item, reason):
    code, out, _ = _evaluate(capsys, *DEFINES, item, "--json")
    assert code == 1
    (record,) = json.loads(out)["evaluations"]
    assert record["state"] == "unevaluable" and record["reason"] == reason


def test_function_like_macro_name_is_reported_not_called(capsys, tmp_path):
    seed = tmp_path / "macros.h"
    seed.write_text("#define SQ(x) ((x) * (x))\n", encoding="utf-8")
    code, out, _ = _evaluate(capsys, "--config-from", str(seed), "SQ", "SQ(3)", "--json")
    first, second = json.loads(out)["evaluations"]
    assert code == 1
    assert first["reason"] == "function_like" and second["value"] == 9


def test_json_document_has_the_schema_first_and_stable_records(capsys):
    code, out, _ = _evaluate(capsys, *DEFINES, "AREA", "1 / 0", "--json")
    data = json.loads(out)
    assert code == 1
    assert list(data) == ["schema", "evaluations"]
    assert data["schema"] == "cpre.evaluations/1"
    assert all("schema" not in record for record in data["evaluations"])
    assert data["evaluations"][0] == {
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


def test_color_adds_only_escape_sequences(capsys):
    _, plain, _ = _evaluate(capsys, *DEFINES, "AREA", "1 +", "--color", "never")
    _, colored, _ = _evaluate(capsys, *DEFINES, "AREA", "1 +", "--color", "always")
    assert "\x1b[" in colored and "\x1b[" not in plain
    assert ANSI.sub("", colored) == plain


def test_unknown_names_policy_and_undef(capsys):
    code, out, _ = _evaluate(capsys, "MISSING + 1", "--unknown-names", "undefined", "--json")
    assert code == 0 and json.loads(out)["evaluations"][0]["value"] == 1
    code, out, _ = _evaluate(capsys, "-U", "OFF", "OFF + 1", "OFF", "--json")
    first, second = json.loads(out)["evaluations"]
    assert code == 1 and first["value"] == 1 and second["reason"] == "unresolved_identifier"


def test_config_from_a_seed_file(capsys, tmp_path):
    seed = tmp_path / "flags.h"
    seed.write_text("#define LEVEL 3\n#define LIMIT (LEVEL * 10)\n", encoding="utf-8")
    code, out, _ = _evaluate(capsys, "--config-from", str(seed), "LIMIT", "-D", "LEVEL=5", "--json")
    assert code == 0
    # -D takes precedence over the seed, and the seed's own macro sees it.
    assert json.loads(out)["evaluations"][0]["value"] == 50


def test_standard_macro_supplies_a_predefined_value(capsys):
    code, out, _ = _evaluate(
        capsys, "--standard-macro", "__STDC_HOSTED__=1", "__STDC_HOSTED__ + 1", "--json"
    )
    assert code == 0 and json.loads(out)["evaluations"][0]["value"] == 2


def test_max_work_limits_each_item(capsys):
    code, out, _ = _evaluate(capsys, "1 + 1 + 1 + 1 + 1 + 1", "--max-work", "3", "--json")
    assert code == 1
    assert json.loads(out)["evaluations"][0]["reason"] == "budget_exceeded"


@pytest.mark.parametrize(
    "argv, message",
    [
        (["-D", "1BAD=2", "1"], "invalid macro name"),
        (["-U", "A=1", "1"], "--undef accepts only a macro name"),
        (["--standard-macro", "__STDC_HOSTED__", "1"], "requires NAME=VALUE"),
        (["--standard-macro", "NOT_STANDARD=1", "1"], "unsupported standard predefined macro"),
        (["-D", "A=1", "-U", "A", "1"], "conflicting concrete configuration"),
    ],
)
def test_invalid_configuration_exits_two(capsys, argv, message):
    code, out, err = _evaluate(capsys, *argv)
    assert code == 2 and out == ""
    assert message in err and err.startswith("cpre evaluate: error:")


@pytest.mark.parametrize(
    ("option", "message"),
    [
        (["--config-exclude", "FOO"], "--config-exclude requires --config-from"),
        (["--config-from-unknown-names", "undefined"], "--config-from-unknown-names requires"),
    ],
)
def test_seed_options_require_a_seed_file(capsys, option, message):
    with pytest.raises(SystemExit) as raised:
        main(["evaluate", "1", *option])
    assert raised.value.code == 2
    assert message in capsys.readouterr().err


def test_unreadable_seed_exits_two(capsys, tmp_path):
    code, out, err = _evaluate(capsys, "--config-from", str(tmp_path / "missing.h"), "1")
    assert code == 2 and out == "" and "missing.h" in err


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_non_positive_work_limit_is_a_usage_error(capsys, limit):
    with pytest.raises(SystemExit) as raised:
        main(["evaluate", "1", "--max-work", limit])
    assert raised.value.code == 2
    assert "max_work must be a positive integer" in capsys.readouterr().err


def test_at_least_one_item_is_required(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["evaluate"])
    assert raised.value.code == 2


def test_module_entry_point():
    run = subprocess.run(
        [sys.executable, "-m", "cpre", "evaluate", "-D", "N=6", "N * 7", "--color", "never"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0
    assert run.stdout.splitlines() == ["- N * 7", "+ 42", "  expanded  6 * 7"]
