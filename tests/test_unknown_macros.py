from __future__ import annotations

import json
from pathlib import Path

import pytest

import cpre
from cpre import MacroConfiguration, MacroDefinition, MacroUse, UnknownMacro, UnknownNamePolicy
from cpre.cli import main

DEFINEDNESS = (MacroUse.DEFINEDNESS,)
VALUE = (MacroUse.VALUE,)
BOTH = (MacroUse.DEFINEDNESS, MacroUse.VALUE)


def _names(unknown: tuple[UnknownMacro, ...]) -> dict[str, tuple[MacroUse, ...]]:
    return {item.name: item.uses for item in unknown}


def _lines(unknown: tuple[UnknownMacro, ...]) -> dict[str, list[int | None]]:
    return {item.name: [location.line for location in item.locations] for item in unknown}


def _unresolved(source: str, **kwargs: object) -> cpre.PreprocessDiagnostic:
    result = cpre.preprocess_source(source, **kwargs)  # type: ignore[arg-type]
    assert not result.complete
    (diagnostic,) = result.incomplete
    assert isinstance(diagnostic, cpre.PreprocessDiagnostic)
    assert diagnostic.code is cpre.ErrorCode.UNRESOLVED_CONDITION
    return diagnostic


# --- structured preprocessing diagnostics ---------------------------------------


def test_gnuc_version_check_names_the_unresolved_macro():
    diagnostic = _unresolved("#if __GNUC__ >= 4\nint x;\n#endif\n")

    assert diagnostic.condition == "__GNUC__ >= 4"
    assert diagnostic.location.line == 1
    assert diagnostic.unresolved == (UnknownMacro("__GNUC__", VALUE, (cpre.SourceLocation(1),)),)
    assert diagnostic.unresolved[0].suggestions == ("-D __GNUC__=<value>", "-U __GNUC__")
    assert "condition is not determined" in diagnostic.message
    assert "__GNUC__ (value)" in diagnostic.message


@pytest.mark.parametrize(
    "source",
    [
        "#if defined(FEAT)\n#endif\n",
        "#if defined FEAT\n#endif\n",
        "#ifdef FEAT\n#endif\n",
        "#ifndef FEAT\n#endif\n",
        "#if 0\n#elifdef FEAT\n#endif\n",
    ],
)
def test_definedness_tests_do_not_suggest_a_value(source):
    diagnostic = _unresolved(source)

    (item,) = diagnostic.unresolved
    assert item.name == "FEAT"
    assert item.uses == DEFINEDNESS
    assert item.suggestions == ("-D FEAT", "-U FEAT")
    assert not any("=" in suggestion for suggestion in item.suggestions)


def test_nested_expression_reports_every_unresolved_name_once_sorted():
    diagnostic = _unresolved("#if ((B + (A * 2)) > C) && !(defined D || A == B)\n#endif\n")

    assert _names(diagnostic.unresolved) == {
        "A": VALUE,
        "B": VALUE,
        "C": VALUE,
        "D": DEFINEDNESS,
    }
    assert [item.name for item in diagnostic.unresolved] == ["A", "B", "C", "D"]


def test_name_used_for_definedness_and_value_reports_both_uses():
    diagnostic = _unresolved("#if defined(FEAT) && FEAT > 2\n#endif\n")

    (item,) = diagnostic.unresolved
    assert item.uses == BOTH
    assert item.suggestions[0] == "-D FEAT=<value>"


def test_known_macros_are_not_reported():
    configuration = MacroConfiguration(integers={"A": 1}, undefined=["U"])
    diagnostic = _unresolved(
        "#if A + B > U + 1 || defined(U)\n#endif\n#if 0\n#endif\n",
        configuration=configuration,
    )
    assert _names(diagnostic.unresolved) == {"B": VALUE}


def test_names_reached_through_macro_expansion_are_reported():
    diagnostic = _unresolved(
        "#define VERSION (__GNUC__ * 100 + MINOR)\n#if VERSION >= 400\n#endif\n"
    )

    assert diagnostic.location.line == 2
    assert _names(diagnostic.unresolved) == {"MINOR": VALUE, "__GNUC__": VALUE}


def test_unresolved_condition_in_nested_branch_uses_state_at_that_point():
    diagnostic = _unresolved(
        "#define A 1\n#if A\n#if A + B\n#endif\n#endif\n",
    )
    assert diagnostic.location.line == 3
    assert _names(diagnostic.unresolved) == {"B": VALUE}


# --- expression-level API -------------------------------------------------------


def test_unknown_macros_of_expression():
    assert _names(cpre.unknown_macros("__GNUC__ >= 4")) == {"__GNUC__": VALUE}
    assert _names(cpre.unknown_macros("defined(FEAT)")) == {"FEAT": DEFINEDNESS}
    assert cpre.unknown_macros("defined(FEAT)")[0].locations == ()


@pytest.mark.parametrize(
    "expression",
    ["0", "1 && 0x10UL > 7", "'a' == 97", "(1 ? 2 : 3) << 1", "-1 < 0u"],
)
def test_literals_are_never_unknown(expression):
    assert cpre.unknown_macros(expression) == ()


def test_unknown_macros_respects_configuration_and_expands_definitions():
    configuration = MacroConfiguration(
        definitions=[MacroDefinition("VERSION", "(MAJOR * 100)"), MacroDefinition("X", "1")],
        undefined=["OLD"],
    )
    assert _names(
        cpre.unknown_macros("VERSION > X && !defined(OLD) && OLD == 0", configuration=configuration)
    ) == {"MAJOR": VALUE}


def test_closed_world_configuration_has_no_unknown_names():
    configuration = MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED)
    assert cpre.unknown_macros("A > 1 || defined(B)", configuration=configuration) == ()


def test_operators_and_location_builtins_are_not_unknown():
    assert cpre.unknown_macros("__LINE__ > 1 && __has_include(<x.h>)") == ()


def test_unknown_macros_rejects_non_string():
    with pytest.raises(cpre.AnalysisError):
        cpre.unknown_macros(1)  # type: ignore[arg-type]


# --- source-level API -----------------------------------------------------------

SOURCE = (
    "#ifndef T_H\n"  # 1
    "#define T_H\n"  # 2
    "#define LEVEL 2\n"  # 3
    "#if __GNUC__ >= 4 && LEVEL > 1\n"  # 4
    "int a;\n"  # 5
    "#elif defined(FEAT) || defined FEAT\n"  # 6
    "int b;\n"  # 7
    "#endif\n"  # 8
    "#ifdef OPT\n"  # 9
    "#define V (X + 1)\n"  # 10
    "#endif\n"  # 11
    "#if V > 2 || __GNUC__ > 9\n"  # 12
    "#endif\n"  # 13
    "#endif\n"  # 14
)


def test_unknown_macros_in_source_reports_every_conditional():
    unknown = cpre.unknown_macros_in_source(SOURCE)

    assert _names(unknown) == {
        "FEAT": DEFINEDNESS,
        "OPT": DEFINEDNESS,
        "T_H": DEFINEDNESS,
        "V": VALUE,
        "X": VALUE,
        "__GNUC__": VALUE,
    }
    # LEVEL is defined on every path before its use; include guards nest bodies.
    assert "LEVEL" not in _names(unknown)
    # Repeated names collect every dependent directive line.
    assert _lines(unknown)["__GNUC__"] == [4, 12]
    assert _lines(unknown)["FEAT"] == [6]


def test_unknown_macros_in_source_respects_configuration():
    configuration = MacroConfiguration(
        integers={"__GNUC__": 5}, undefined=["OPT", "T_H"], presence=["FEAT"]
    )
    unknown = cpre.unknown_macros_in_source(SOURCE, configuration=configuration)

    # V is still unknown: the external value is used when OPT is undefined.
    assert _names(unknown) == {"V": VALUE, "X": VALUE}


def test_unknown_macros_in_source_definition_after_use_does_not_hide_it():
    unknown = cpre.unknown_macros_in_source("#if A\n#endif\n#define A 1\n#if A\n#endif\n")
    assert _lines(unknown) == {"A": [1]}


def test_unknown_macros_in_source_elif_is_evaluated_outside_its_branch():
    unknown = cpre.unknown_macros_in_source("#if X\n#define A 1\n#elif A\n#endif\n")
    assert _names(unknown) == {"A": VALUE, "X": VALUE}


def test_undef_fixes_a_name():
    assert cpre.unknown_macros_in_source("#undef A\n#if A || defined(A)\n#endif\n") == ()


def test_unknown_macros_in_source_raises_parse_errors():
    with pytest.raises(cpre.ParseError):
        cpre.unknown_macros_in_source("#if A\n", filename="broken.c")


def test_seeding_every_unknown_macro_makes_preprocessing_complete():
    unknown = cpre.unknown_macros_in_source(SOURCE)
    configuration = MacroConfiguration(
        definitions=[
            MacroDefinition(item.name, "1") for item in unknown if MacroUse.VALUE in item.uses
        ],
        undefined=[item.name for item in unknown if MacroUse.VALUE not in item.uses],
    )
    assert cpre.preprocess_source(SOURCE, configuration=configuration).complete


# --- CLI ------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def test_cli_text_diagnostic_hints_command_line_forms(tmp_path, capsys):
    target = _write(tmp_path / "t.c", "#if __GNUC__ >= 4 && defined(FEAT)\n#endif\n")

    assert main(["preprocess", str(target)]) == 2
    err = capsys.readouterr().err.splitlines()
    assert err == [
        f"{target}: line 1: condition is not determined by the current macro state: "
        "#if __GNUC__ >= 4 && defined(FEAT) (unresolved: FEAT (definedness), "
        "__GNUC__ (value))",
        f"{target}: hint: supply -D FEAT or -U FEAT",
        f"{target}: hint: supply -D __GNUC__=<value>, or -U __GNUC__ to evaluate it as 0",
    ]


def test_cli_json_failure_contains_structured_unresolved_names(tmp_path, capsys):
    target = _write(tmp_path / "t.c", "int x;\n#ifdef FEAT\n#endif\n")

    assert main(["preprocess", "--json", str(target)]) == 2
    document = json.loads(capsys.readouterr().out)
    assert document["complete"] is False
    assert document["source"] is None
    (diagnostic,) = document["diagnostics"]
    assert diagnostic["code"] == "unresolved_condition"
    assert diagnostic["line"] == 2
    assert diagnostic["condition"] == "FEAT"
    assert diagnostic["unresolved"] == [
        {
            "name": "FEAT",
            "uses": ["definedness"],
            "lines": [2],
            "suggestions": ["-D FEAT", "-U FEAT"],
        }
    ]


def test_cli_json_success_contains_source(tmp_path, capsys):
    target = _write(tmp_path / "t.c", "#ifdef FEAT\nint x;\n#endif\n")

    assert main(["preprocess", "--json", "-D", "FEAT", "--compact", str(target)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document == {
        "file": str(target),
        "complete": True,
        "source": "int x;\n",
        "diagnostics": [],
        "skipped_includes": [],
    }


def test_cli_list_unknown_macros_text_is_deterministic(tmp_path, capsys):
    target = _write(tmp_path / "t.c", SOURCE)

    assert main(["preprocess", "--list-unknown-macros", str(target)]) == 0
    first = capsys.readouterr().out
    assert main(["preprocess", "--list-unknown-macros", str(target)]) == 0
    assert capsys.readouterr().out == first
    assert first.splitlines() == [
        "FEAT\tdefinedness\t6",
        "OPT\tdefinedness\t9",
        "T_H\tdefinedness\t1",
        "V\tvalue\t12",
        "X\tvalue\t12",
        "__GNUC__\tvalue\t4,12",
    ]


def test_cli_list_unknown_macros_honors_configuration_and_json(tmp_path, capsys):
    target = _write(tmp_path / "t.c", SOURCE)
    seed = _write(tmp_path / "flags.h", "#define __GNUC__ 5\n#undef OPT\n")

    argv = ["preprocess", "--list-unknown-macros", "--json", "--config-from", str(seed)]
    assert main([*argv, "-U", "T_H", "-D", "FEAT", str(target)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["file"] == str(target)
    assert [item["name"] for item in document["unknown_macros"]] == ["V", "X"]
    assert document["unknown_macros"][0] == {
        "name": "V",
        "uses": ["value"],
        "lines": [12],
        "suggestions": ["-D V=<value>", "-U V"],
    }


def test_cli_list_unknown_macros_builds_a_seed_for_preprocessing(tmp_path, capsys):
    target = _write(tmp_path / "t.c", SOURCE)

    assert main(["preprocess", "--list-unknown-macros", str(target)]) == 0
    seed_lines = []
    for record in capsys.readouterr().out.splitlines():
        name, uses, _ = record.split("\t")
        seed_lines.append(f"#define {name} 9" if "value" in uses else f"#undef {name}")
    seed = _write(tmp_path / "seed.h", "\n".join(seed_lines) + "\n")

    assert main(["preprocess", "--config-from", str(seed), str(target)]) == 0
    assert "int a;" in capsys.readouterr().out


def test_cli_list_unknown_macros_rejects_compact(tmp_path, capsys):
    target = _write(tmp_path / "t.c", SOURCE)
    with pytest.raises(SystemExit) as excinfo:
        main(["preprocess", "--list-unknown-macros", "--compact", str(target)])
    assert excinfo.value.code == 2
    assert "--compact" in capsys.readouterr().err
