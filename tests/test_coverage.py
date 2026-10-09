from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import cpre
from cpre import BranchOutcomeStatus as Status
from cpre import WitnessAssignment, WitnessAtomKind, cover_branches

COVERED = Status.COVERED


def _definitions(configuration: cpre.MacroConfiguration) -> dict[str, str]:
    return {item.name: item.replacement for item in configuration.definitions}


def _assert_verified(source: str, result: cpre.BranchCoverageResult, **kwargs) -> None:
    """Re-run every configuration and check the outcomes it claims by branch body."""
    assert result.complete and result.configurations is not None and result.outcomes is not None
    lines = source.splitlines()
    marker = {
        outcome.branch_line: lines[outcome.branch_line].strip()
        for outcome in result.outcomes
        if outcome.branch_line is not None
    }
    for number, item in enumerate(result.configurations):
        output = cpre.preprocess_source(source, configuration=item.configuration, **kwargs)
        assert output.complete
        retained = {line.strip() for line in output.source.splitlines()}
        for position in item.covers:
            outcome = result.outcomes[position]
            group = [
                other.branch_line
                for other in result.outcomes
                if other.group_line == outcome.group_line and other.branch_line is not None
            ]
            for branch_line in group:
                if not marker[branch_line].startswith("int "):
                    continue  # the body opens with a nested directive
                assert (marker[branch_line] in retained) == (branch_line == outcome.branch_line)
        assert item.covers, number
    for position, outcome in enumerate(result.outcomes):
        if outcome.status is COVERED:
            assert position in result.configurations[outcome.configuration].covers
        else:
            assert outcome.configuration is None and outcome.reason


def _summary(result: cpre.BranchCoverageResult):
    assert result.outcomes is not None and result.configurations is not None
    return (
        [_definitions(item.configuration) for item in result.configurations],
        [(o.group_line, o.branch_line, o.status, o.configuration) for o in result.outcomes],
    )


def test_single_macro_covers_both_outcomes():
    source = "#if A\nint a;\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    assert _summary(result) == (
        [{"A": "1"}, {}],
        [(1, 1, COVERED, 0), (1, None, COVERED, 1)],
    )
    first, second = result.configurations
    assert first.configuration.undefined == frozenset()
    assert second.configuration.undefined == {"A"}
    assert first.configuration.unknown_names is cpre.UnknownNamePolicy.UNDEFINED
    # A true value implies definedness, so definedness is not separately required.
    assert first.required == (WitnessAssignment(WitnessAtomKind.MACRO_VALUE, "A", True),)
    assert second.required == (WitnessAssignment(WitnessAtomKind.MACRO_VALUE, "A", False),)


def test_conjunction_and_disjunction():
    conjunction = "#if A && B\nint ab;\n#endif\n"
    result = cover_branches(conjunction)
    _assert_verified(conjunction, result)
    assert _summary(result)[0] == [{"A": "1", "B": "1"}, {}]

    disjunction = "#if A || B\nint ab;\n#else\nint none;\n#endif\n"
    result = cover_branches(disjunction)
    _assert_verified(disjunction, result)
    assert _summary(result) == (
        [{"B": "1"}, {}],
        [(1, 1, COVERED, 0), (1, 3, COVERED, 1)],
    )
    assert result.configurations[0].configuration.undefined == {"A"}


def test_elif_chain_and_negation():
    source = (
        "#if !A\nint not_a;\n#elif B\nint b;\n#elif defined(C)\nint c;\n#else\nint other;\n#endif\n"
    )
    result = cover_branches(source)
    _assert_verified(source, result)
    assert [outcome.status for outcome in result.outcomes] == [COVERED] * 4
    assert len(result.configurations) == 4


def test_dont_care_macros_are_reported():
    source = "#ifdef A\nint a;\n#else\n#if B\nint b;\n#endif\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    first = result.configurations[0]
    assert _definitions(first.configuration) == {"A": "1"}
    assert first.dont_care == ("B",)
    # Don't-cares are still pinned in the concrete configuration.
    assert first.configuration.undefined == {"B"}


def test_independent_groups_share_configurations():
    source = "#if A\nint a;\n#endif\n#if B\nint b;\n#endif\n#ifdef C\nint c;\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    assert len(result.configurations) == 2
    assert _summary(result)[0] == [{"A": "1", "B": "1", "C": "1"}, {}]


def test_redundant_configurations_are_removed():
    source = "#if A\nint a1;\n#endif\n#if A && B\nint ab;\n#endif\n#if A\nint a2;\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    covered = [len(item.covers) for item in result.configurations]
    assert len(result.configurations) == 2
    assert sum(covered) == len(result.outcomes)


def test_unsatisfiable_outcomes_are_identified():
    source = (
        "#if A && !A\nint never;\n#endif\n"
        "#if 0\n#if B\nint dead;\n#endif\n#endif\n"
        "#if 1\nint always;\n#else\nint never_else;\n#endif\n"
    )
    result = cover_branches(source)
    _assert_verified(source, result)
    statuses = {(o.group_line, o.branch_line): o.status for o in result.outcomes}
    assert statuses == {
        (1, 1): Status.UNSATISFIABLE,
        (1, None): COVERED,
        (4, 4): Status.UNSATISFIABLE,
        (5, 5): Status.UNSATISFIABLE,
        (5, None): Status.UNSATISFIABLE,
        (4, None): COVERED,
        (9, 9): COVERED,
        (9, 11): Status.UNSATISFIABLE,
    }
    reason = next(o.reason for o in result.outcomes if o.status is Status.UNSATISFIABLE)
    assert reason == "the outcome's path condition is unsatisfiable"


def test_source_order_define_is_not_claimed_as_covered():
    source = "#define A 1\n#if A\nint a;\n#else\nint not_a;\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    branch, otherwise = result.outcomes
    assert branch.status is COVERED
    assert otherwise.status is Status.NOT_COVERED
    assert "the source defines or undefines A" in otherwise.reason


def test_opaque_comparison_is_not_given_invented_values():
    source = "#if VERSION >= 3\nint modern;\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    modern, older = result.outcomes
    assert modern.status is Status.NOT_COVERED
    assert "`VERSION >= 3`" in modern.reason
    assert "does not invent integer values" in modern.reason
    assert older.status is COVERED


def test_include_guard_and_nested_outcomes():
    source = "#ifndef GUARD_H\n#define GUARD_H\n#ifdef FEATURE\nint feature;\n#endif\n#endif\n"
    result = cover_branches(source)
    _assert_verified(source, result)
    assert [o.status for o in result.outcomes] == [COVERED] * 4


def test_context_names_are_not_configured():
    context = cpre.PreprocessingContext(standard_macros={"__STDC__": "1"})
    source = "#ifdef __STDC__\nint stdc;\n#endif\n#if A\nint a;\n#endif\n"
    result = cover_branches(source, context=context)
    _assert_verified(source, result, context=context)
    statuses = {(o.group_line, o.branch_line): o.status for o in result.outcomes}
    assert statuses[(1, 1)] is COVERED
    assert statuses[(1, None)] is Status.NOT_COVERED
    assert all(
        "__STDC__" not in _definitions(item.configuration)
        and "__STDC__" not in item.configuration.undefined
        for item in result.configurations
    )


def test_skipped_includes_keep_configured_names_resolvable():
    source = '#include "config.h"\n#ifdef A\nint a;\n#endif\n'
    result = cover_branches(source, skip_includes=True)
    _assert_verified(source, result, skip_includes=True)
    assert [o.status for o in result.outcomes] == [COVERED, COVERED]


def test_incomplete_preprocessing_is_reported_as_not_covered():
    source = '#include "config.h"\n#ifdef A\nint a;\n#endif\n'
    result = cover_branches(source)
    assert result.complete
    assert result.configurations == ()
    assert all(o.status is Status.NOT_COVERED for o in result.outcomes)
    assert result.outcomes[0].reason.startswith("preprocessing was incomplete: line 1: ")


def test_resource_limit_is_atomic_incomplete():
    source = "".join(f"#if A{index} && B{index}\nint x{index};\n#endif\n" for index in range(8))
    result = cover_branches(source, options=cpre.AnalysisOptions(max_work=20))
    assert not result.complete
    assert result.configurations is None and result.outcomes is None
    assert result.incomplete.code is cpre.ErrorCode.ANALYSIS_LIMIT_EXCEEDED


def test_malformed_conditionals_raise_parse_error():
    with pytest.raises(cpre.ParseError):
        cover_branches("#if A &&\n#endif\n", filename="bad.c")


def test_no_conditionals():
    result = cover_branches("int x;\n")
    assert result.complete
    assert result.configurations == () and result.outcomes == ()


def test_output_is_deterministic_across_hash_seeds():
    script = (
        "import cpre\n"
        "source = '#if B || A\\n#elif C && D\\n#endif\\n#ifdef E\\n#if F\\n#endif\\n#endif\\n'\n"
        "result = cover_branches(source)\n"
        "print(result.outcomes)\n"
        "for item in result.configurations:\n"
        "    configuration = item.configuration\n"
        "    print(configuration.definitions, sorted(configuration.undefined))\n"
        "    print(item.required, item.dont_care, item.covers)\n"
    ).replace("cover_branches(", "cpre.cover_branches(")
    root = Path(__file__).resolve().parents[1]
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(root)},
        ).stdout
        for seed in ("0", "1", "2")
    }
    assert len(outputs) == 1


def test_independent_components_are_combined():
    source = (
        "#if A\nint a;\n#elif B\nint b;\n#elif C\nint c;\n#else\nint other;\n#endif\n"
        "#ifdef D\nint d;\n#endif\n"
        "#if E && F\nint ef;\n#endif\n"
    )
    result = cover_branches(source)
    _assert_verified(source, result)
    assert all(outcome.status is COVERED for outcome in result.outcomes)
    # The four-way group needs four configurations; the others ride along.
    assert len(result.configurations) == 4


def test_verification_resource_limit_is_atomic_incomplete():
    # Each component is small, but concrete preprocessing of the whole file is not.
    source = "".join(f"#ifdef F{index}\nint f{index};\n#endif\n" for index in range(40))
    result = cover_branches(source)
    assert not result.complete
    assert result.configurations is None and result.outcomes is None
    assert result.incomplete.code is cpre.ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert result.incomplete.resource == "atoms"

    raised = cover_branches(source, options=cpre.AnalysisOptions(max_atoms=100))
    _assert_verified(source, raised, options=cpre.AnalysisOptions(max_atoms=100))
    assert len(raised.configurations) == 2
