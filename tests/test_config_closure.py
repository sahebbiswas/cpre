"""Block rewrites follow macros from a configuration, not just the file (issue #246)."""

from __future__ import annotations

import json

import pytest
from block_rewrite_support import findings_of

import cpre
from cpre.cli import main

MERGE = cpre.FindingKind.MERGEABLE_BLOCKS
ELSE = cpre.FindingKind.COMPLEMENTARY_BLOCKS

# FEATURE is defined in a header as ``LEVEL``; the first body undefines ``LEVEL``.
HEADER_DEPENDENT = "#if FEATURE\n#undef LEVEL\n#endif\n\n#if FEATURE\nint y;\n#endif\n"


def _config(*definitions: cpre.MacroDefinition, **kwargs) -> cpre.MacroConfiguration:
    return cpre.MacroConfiguration(definitions=definitions, **kwargs)


def _kinds(source: str, **kwargs) -> list[cpre.FindingKind]:
    result = cpre.analyze_source(source, **kwargs)
    assert result.complete
    return [finding.kind for finding in result.findings]


# --- The configuration is followed ---------------------------------------------------------


def test_without_a_configuration_the_pair_is_reported_as_before():
    assert _kinds(HEADER_DEPENDENT) == [MERGE]


def test_a_configuration_macro_that_a_body_changes_keeps_the_pair_apart():
    configuration = _config(cpre.MacroDefinition("FEATURE", "LEVEL"))

    assert _kinds(HEADER_DEPENDENT, configuration=configuration) == []


def test_the_dependency_is_followed_through_several_configuration_macros():
    configuration = _config(
        cpre.MacroDefinition("FEATURE", "MID"), cpre.MacroDefinition("MID", "LEVEL")
    )

    assert _kinds(HEADER_DEPENDENT, configuration=configuration) == []


def test_a_configuration_macro_chain_can_end_in_a_file_macro():
    source = "#define LEVEL 1\n" + HEADER_DEPENDENT
    configuration = _config(cpre.MacroDefinition("FEATURE", "LEVEL"))

    assert _kinds(source, configuration=configuration) == []


def test_a_configuration_that_does_not_involve_the_body_changes_nothing():
    configuration = _config(cpre.MacroDefinition("FEATURE", "OTHER"))

    assert _kinds(HEADER_DEPENDENT, configuration=configuration) == [MERGE]
    assert _kinds(
        HEADER_DEPENDENT, configuration=cpre.MacroConfiguration(presence=["FEATURE"])
    ) == [MERGE]


def test_an_undefined_name_in_the_configuration_adds_no_dependency():
    configuration = cpre.MacroConfiguration(undefined=["FEATURE"])

    assert _kinds(HEADER_DEPENDENT, configuration=configuration) == [MERGE]


def test_the_configuration_is_not_an_assumption():
    configuration = _config(cpre.MacroDefinition("OTHER", "1"))
    (finding,) = findings_of(HEADER_DEPENDENT, MERGE, configuration=configuration)

    assert finding.depends_on_assumptions is False


def test_a_configuration_and_assumptions_can_be_given_together():
    configuration = _config(cpre.MacroDefinition("FEATURE", "LEVEL"))

    assert _kinds(HEADER_DEPENDENT, configuration=configuration, assumptions={"Z": True}) == []
    assert _kinds(HEADER_DEPENDENT, assumptions={"Z": True}) == [MERGE]


# --- The regroup, pragma and volatile rules reach header macros ---------------------------


@pytest.mark.parametrize(
    "definition",
    [
        cpre.MacroDefinition("A", "X || Y"),
        cpre.MacroDefinition("A", "(a) + 1", parameters=("a",)),
        cpre.MacroDefinition("A", "X ? Y : Z"),
    ],
)
def test_a_header_macro_that_regroups_blocks_a_complementary_pair(definition):
    source = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"

    assert _kinds(source) == [ELSE]
    assert _kinds(source, configuration=_config(definition)) == []


def test_a_header_macro_that_regroups_blocks_a_rewrite_of_differently_written_tests():
    source = "#if A && B\nx;\n#endif\n#if B && A\nx;\n#endif\n"
    configuration = _config(cpre.MacroDefinition("A", "X || Y"))

    assert _kinds(source) == [MERGE]
    assert _kinds(source, configuration=configuration) == []


def test_a_parenthesized_header_macro_does_not_block_a_pair():
    source = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"
    configuration = _config(cpre.MacroDefinition("A", "(X || Y)"))

    assert _kinds(source, configuration=configuration) == [ELSE]


@pytest.mark.parametrize(
    "replacement",
    ["__COUNTER__", "__LINE__", "(__COUNTER__ + 1)", '_Pragma("once")'],
)
def test_a_header_macro_that_is_volatile_blocks_the_pair(replacement):
    source = "#if A\nx;\n#endif\n#if A\nx;\n#endif\n"

    assert _kinds(source) == [MERGE]
    assert _kinds(source, configuration=_config(cpre.MacroDefinition("A", replacement))) == []


def test_a_header_macro_that_pastes_names_blocks_the_pair():
    source = "#if A\nx;\n#endif\n#if A\nx;\n#endif\n"
    configuration = _config(cpre.MacroDefinition("A", "P(__COUN, TER__)"))

    assert _kinds(source, configuration=configuration) == [MERGE]  # No ## in this definition.
    paste = _config(cpre.MacroDefinition("A", "X##Y"))
    assert _kinds(source, configuration=paste) == []


def test_a_later_condition_reaching_line_through_a_header_macro_blocks_the_rewrite():
    source = "#if A\nx;\n#endif\n#if A\nx;\n#endif\n#if L > 3\ny;\n#endif\n"
    configuration = _config(cpre.MacroDefinition("L", "__LINE__"))

    assert MERGE in _kinds(source)
    assert _kinds(source, configuration=configuration) == []


def test_the_configuration_also_reaches_the_other_rewrite_kinds():
    source = "#if A\nx;\n#elif B\nx;\n#endif\n"
    configuration = _config(cpre.MacroDefinition("B", "__COUNTER__"))

    assert _kinds(source) == [cpre.FindingKind.JOINABLE_BRANCHES]
    assert _kinds(source, configuration=configuration) == []


# --- Strict merging ------------------------------------------------------------------------

UNRELATED_DEFINE = "#if FEATURE\n#define Z 1\n#endif\n#if FEATURE\ny;\n#endif\n"


def test_strict_merge_keeps_a_pair_apart_when_a_name_is_not_defined_anywhere():
    assert _kinds(UNRELATED_DEFINE) == [MERGE]
    assert _kinds(UNRELATED_DEFINE, strict_merge=True) == []


def test_strict_merge_accepts_names_the_file_or_the_configuration_defines():
    defined_here = "#define FEATURE 1\n" + UNRELATED_DEFINE

    assert _kinds(defined_here, strict_merge=True) == [MERGE]
    assert _kinds(
        UNRELATED_DEFINE,
        strict_merge=True,
        configuration=_config(cpre.MacroDefinition("FEATURE", "1")),
    ) == [MERGE]
    assert _kinds(
        UNRELATED_DEFINE,
        strict_merge=True,
        configuration=cpre.MacroConfiguration(undefined=["FEATURE"]),
    ) == [MERGE]


def test_strict_merge_treats_a_closed_world_configuration_as_knowing_every_name():
    configuration = cpre.MacroConfiguration(unknown_names="undefined")

    assert _kinds(UNRELATED_DEFINE, strict_merge=True, configuration=configuration) == [MERGE]


def test_strict_merge_leaves_bodies_without_macro_changes_alone():
    source = "#if FEATURE\nx;\n#endif\n#if FEATURE\ny;\n#endif\n"

    assert _kinds(source, strict_merge=True) == [MERGE]


# --- Command line ----------------------------------------------------------------------------


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "a.c"
    path.write_text(HEADER_DEPENDENT)
    return path


def _run(capsys, *argv):
    code = main(["--color", "never", "--no-macros", *argv])
    return code, capsys.readouterr()


def test_define_on_the_command_line_is_followed(source_file, capsys):
    code, plain = _run(capsys, str(source_file))
    assert code == 0
    assert "MERGE" in plain.out

    code, defined = _run(capsys, "-D", "FEATURE=LEVEL", str(source_file))
    assert code == 0
    assert "MERGE" not in defined.out


def test_undef_on_the_command_line_adds_no_dependency(source_file, capsys):
    _, result = _run(capsys, "-U", "FEATURE", str(source_file))

    assert "MERGE" in result.out


def test_a_seed_header_is_followed(source_file, tmp_path, capsys):
    seed = tmp_path / "config.h"
    seed.write_text("#define FEATURE LEVEL\n")

    _, result = _run(capsys, "--config-from", str(seed), str(source_file))

    assert "MERGE" not in result.out


def test_config_exclude_removes_a_seed_macro(source_file, tmp_path, capsys):
    seed = tmp_path / "config.h"
    seed.write_text("#define FEATURE LEVEL\n")

    _, result = _run(
        capsys, "--config-from", str(seed), "--config-exclude", "FEATURE", str(source_file)
    )

    assert "MERGE" in result.out


def test_a_bad_seed_is_reported_and_exits_2(source_file, tmp_path, capsys):
    code, result = _run(capsys, "--config-from", str(tmp_path / "missing.h"), str(source_file))

    assert code == 2
    assert "missing.h" in result.err


def test_strict_merge_on_the_command_line(tmp_path, capsys):
    path = tmp_path / "b.c"
    path.write_text(UNRELATED_DEFINE)

    _, plain = _run(capsys, str(path))
    _, strict = _run(capsys, "--strict-merge", str(path))

    assert "MERGE" in plain.out
    assert "MERGE" not in strict.out


def test_json_and_sarif_follow_the_configuration(source_file, capsys):
    main(["--json", "--verbose", "--no-macros", "-D", "FEATURE=LEVEL", str(source_file)])
    (group, *_) = json.loads(capsys.readouterr().out)["groups"]
    assert group["merge_with"] == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--config-exclude", "A"],
        ["--config-from-unknown-names", "undefined"],
        ["-D", "1bad"],
        ["-U", "A=1"],
    ],
)
def test_malformed_configuration_options_are_usage_errors(source_file, capsys, argv):
    with pytest.raises(SystemExit) as stopped:
        main([*argv, str(source_file)])

    assert stopped.value.code == 2


@pytest.mark.parametrize("option", [["-D", "A"], ["-U", "A"], ["--strict-merge"]])
def test_configuration_options_conflict_with_no_conditionals(source_file, capsys, option):
    with pytest.raises(SystemExit) as stopped:
        main(["--no-conditionals", *option, str(source_file)])

    assert stopped.value.code == 2
    assert "--no-conditionals" in capsys.readouterr().err


def test_the_default_command_does_not_take_a_name_policy_or_standard_macros(source_file):
    for option in (["--unknown-names", "undefined"], ["--standard-macro", "A=1"]):
        with pytest.raises(SystemExit) as stopped:
            main([*option, str(source_file)])
        assert stopped.value.code == 2


def test_a_configuration_of_the_wrong_type_is_rejected_up_front():
    for bad in ({"a": 1}, "config.h", 3):
        with pytest.raises(cpre.AnalysisError):
            cpre.analyze_source("int x;\n", configuration=bad)  # type: ignore[arg-type]


def test_closed_world_flag_makes_strict_merge_treat_unlisted_names_as_known(tmp_path, capsys):
    from cpre.cli import main

    path = tmp_path / "a.c"
    path.write_text("#if A\n#define B 1\n#endif\n#if A\nx;\n#endif\n")
    assert main(["--json", "--strict-merge", str(path)]) == 0
    open_world = capsys.readouterr().out
    assert main(["--json", "--strict-merge", "--closed-world", str(path)]) == 0
    closed_world = capsys.readouterr().out
    assert "mergeable_blocks" not in open_world or "mergeable_blocks" in closed_world


def test_analysis_help_says_the_options_do_not_evaluate_conditions(capsys):
    from cpre.cli import main

    with pytest.raises(SystemExit):
        main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "not used to evaluate conditions" in text
    assert "--closed-world" in text


# --- Review follow-ups ---------------------------------------------------------------------

JOIN = cpre.FindingKind.JOINABLE_BRANCHES
LATER_CALL = "#if A\nx;\n#elif F(1)\nx;\n#endif\n"


def test_a_call_to_a_configured_object_like_macro_is_not_a_known_call():
    configuration = _config(cpre.MacroDefinition("F", "1"))

    assert JOIN not in _kinds(LATER_CALL, configuration=configuration)


def test_a_call_to_a_configured_function_like_macro_is_a_known_call():
    function_like = _config(cpre.MacroDefinition("F", "X", parameters=("X",)))

    assert JOIN in _kinds(LATER_CALL, configuration=function_like)
    assert JOIN not in _kinds(LATER_CALL)


def test_a_call_to_an_object_like_macro_of_the_file_is_not_a_known_call():
    assert JOIN not in _kinds("#define F 1\n" + LATER_CALL)
    assert JOIN in _kinds("#define F(X) X\n" + LATER_CALL)


def test_strict_merge_does_not_count_a_definition_that_comes_later():
    source = UNRELATED_DEFINE + "#define FEATURE 1\n"

    assert _kinds(source, strict_merge=True) == []
    assert _kinds("#define FEATURE 1\n" + UNRELATED_DEFINE, strict_merge=True) == [MERGE]


def test_an_integer_suffix_is_not_a_macro_name():
    configuration = _config(cpre.MacroDefinition("FEATURE", "1U"))

    assert _kinds(UNRELATED_DEFINE, strict_merge=True, configuration=configuration) == [MERGE]
