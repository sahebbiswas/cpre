"""Adjacent #if/#elif branches with the same body are joined with || (issue #244)."""

from __future__ import annotations

import json

import pytest
from block_rewrite_support import apply_edit, findings_of, outputs, rewritten

import cpre
from cpre.cli import main
from cpre.reporting import FindingCounts
from cpre.sarif import sarif_log
from cpre.text_reports import summary_line

KIND = cpre.FindingKind.JOINABLE_BRANCHES


def _joins(source: str, **kwargs) -> list[cpre.Finding]:
    return findings_of(source, KIND, **kwargs)


def _lines(finding: cpre.Finding) -> tuple[int, tuple[int, ...]]:
    return finding.location.line, tuple(item.line for item in finding.related_locations)


# --- What is reported ------------------------------------------------------------------------


def test_two_adjacent_branches_with_the_same_body_are_one_finding_with_an_exact_edit():
    source = "#if A\nreturn 1;\n#elif B\nreturn 1;\n#elif C\nreturn 2;\n#endif\n"

    (finding,) = _joins(source)

    assert _lines(finding) == (1, (3,))
    assert finding.directive == "if"
    assert finding.original_condition == "A"
    assert finding.depends_on_assumptions is False
    assert "lines 1 and 3" in finding.reason
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert apply_edit(source, finding.edit) == "#if A || B\nreturn 1;\n#elif C\nreturn 2;\n#endif\n"


def test_a_run_of_three_is_one_finding():
    source = "#if A\nx;\n#elif B\nx;\n#elif C\nx;\n#else\ny;\n#endif\n"

    (finding,) = _joins(source)

    assert _lines(finding) == (1, (3, 5))
    assert rewritten(source, KIND) == "#if A || B || C\nx;\n#else\ny;\n#endif\n"


def test_a_run_in_the_middle_of_a_chain_keeps_its_elif():
    source = "#if A\nw;\n#elif B\nx;\n#elif C\nx;\n#elif D\nz;\n#endif\n"

    assert rewritten(source, KIND) == "#if A\nw;\n#elif B || C\nx;\n#elif D\nz;\n#endif\n"


def test_two_separate_runs_in_one_chain_are_two_findings():
    source = "#if A\nx;\n#elif B\nx;\n#elif C\ny;\n#elif D\ny;\n#endif\n"

    first, second = _joins(source)

    assert _lines(first) == (1, (3,))
    assert _lines(second) == (5, (7,))
    assert first.edit is not None and second.edit is not None
    assert first.edit.range.end.line <= second.edit.range.start.line


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#ifdef A\nx;\n#elifdef B\nx;\n#endif\n", "#if defined(A) || defined(B)\nx;\n#endif\n"),
        (
            "#ifndef A\nx;\n#elifndef B\nx;\n#endif\n",
            "#if !defined(A) || !defined(B)\nx;\n#endif\n",
        ),
        (
            "#ifdef A\nx;\n#elif defined(B)\nx;\n#endif\n",
            "#if defined(A) || defined(B)\nx;\n#endif\n",
        ),
        ("#if A\nx;\n#elifndef B\nx;\n#endif\n", "#if A || !defined(B)\nx;\n#endif\n"),
        ("#if A && B\nx;\n#elif !C\nx;\n#endif\n", "#if (A && B) || !C\nx;\n#endif\n"),
        ("#if A > 1\nx;\n#elif B\nx;\n#endif\n", "#if (A > 1) || B\nx;\n#endif\n"),
        (
            "#define BAR(a) a\n#if FOO(1)\nx;\n#elif BAR(2)\nx;\n#endif\n",
            "#define BAR(a) a\n#if (FOO(1)) || (BAR(2))\nx;\n#endif\n",
        ),
        ("#if A\nx;\n#elif B\nx;\n#endif\n", "#if A || B\nx;\n#endif\n"),
    ],
)
def test_the_joined_condition_for_each_directive_form(source, expected):
    assert rewritten(source, KIND) == expected


def test_a_condition_with_a_conditional_operator_is_parenthesized():
    # ``A ? B : C || D`` would group differently, so the operands are wrapped.
    source = "#if A ? B : C\nx;\n#elif D\nx;\n#endif\n"

    assert rewritten(source, KIND) == "#if (A ? B : C) || D\nx;\n#endif\n"


def test_a_name_defined_with_a_conditional_operator_is_parenthesized():
    source = "#define M X ? Y : Z\n#if M\nx;\n#elif D\nx;\n#endif\n"

    assert rewritten(source, KIND) == "#define M X ? Y : Z\n#if (M) || D\nx;\n#endif\n"


def test_indentation_and_line_endings_are_kept():
    assert rewritten("  #if A\n  x;\n  #elif B\n  x;\n  #endif\n", KIND) == (
        "  #if A || B\n  x;\n  #endif\n"
    )
    assert rewritten("#if A\r\nx;\r\n#elif B\r\nx;\r\n#endif\r\n", KIND) == (
        "#if A || B\r\nx;\r\n#endif\r\n"
    )


def test_a_run_inside_a_parent_branch_is_found():
    source = "#if P\n#if A\nx;\n#elif B\nx;\n#endif\n#endif\n"

    assert rewritten(source, KIND) == "#if P\n#if A || B\nx;\n#endif\n#endif\n"


def test_a_nested_block_in_both_bodies_counts_as_the_same_body():
    source = "#if A\n#if C\nx;\n#endif\n#elif B\n#if C\nx;\n#endif\n#endif\n"

    assert rewritten(source, KIND) == "#if A || B\n#if C\nx;\n#endif\n#endif\n"


def test_a_body_that_defines_a_macro_is_joined_because_branches_are_exclusive():
    # B is only tested when A was false, so the first body cannot have changed it.
    source = "#if A\n#define B 1\n#elif B\n#define B 1\n#endif\n"

    assert len(_joins(source)) == 1
    assert outputs(source, ("A", "B")) == outputs(rewritten(source, KIND), ("A", "B"))


# --- What is not reported --------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # The bodies differ, even by whitespace or a comment.
        "#if A\nx;\n#elif B\ny;\n#endif\n",
        "#if A\nx;\n#elif B\n x;\n#endif\n",
        "#if A\nx; // a\n#elif B\nx; // b\n#endif\n",
        "#if A\nx;\n#elif B\nx;\n\n#endif\n",
        # Not adjacent.
        "#if A\nx;\n#elif B\ny;\n#elif C\nx;\n#endif\n",
        # An #else is never joined.
        "#if A\nx;\n#else\nx;\n#endif\n",
        "#if A\nx;\n#elif B\ny;\n#else\ny;\n#endif\n",
        # A comment on a directive the rewrite would change or delete.
        "#if A // a\nx;\n#elif B\nx;\n#endif\n",
        "#if A\nx;\n#elif B // b\nx;\n#endif\n",
        "#if A\nx;\n#elif B /* b */\nx;\n#endif\n",
        # A single branch, or separate blocks.
        "#if A\nx;\n#endif\n",
        "#if A\nx;\n#endif\n#if B\nx;\n#endif\n",
        # Volatile conditions: evaluating them fewer times, or on another line, differs.
        "#if __COUNTER__\nx;\n#elif B\nx;\n#endif\n",
        "#if A\nx;\n#elif __LINE__ > 3\nx;\n#endif\n",
        "#define N __LINE__\n#if A\nx;\n#elif N > 3\nx;\n#endif\n",
    ],
)
def test_branches_that_cannot_be_joined_are_not_reported(source):
    assert _joins(source) == []


def test_a_directive_split_over_lines_is_not_reported():
    assert _joins("#if A \\\n && B\nx;\n#elif C\nx;\n#endif\n") == []
    assert _joins("#if A\nx;\n#elif B \\\n && C\nx;\n#endif\n") == []


def test_a_dead_or_redundant_branch_is_reported_as_such_not_joined():
    source = "#if A\nx;\n#elif A\nx;\n#endif\n"

    result = cpre.analyze_source(source)

    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


def test_assumptions_can_only_remove_the_finding():
    source = "#if A\nx;\n#elif B\nx;\n#endif\n"

    assert [f.depends_on_assumptions for f in _joins(source, assumptions={"C": True})] == [False]
    # Assuming ``A`` makes the first branch redundant and the second dead, so they are reported
    # as such; assuming it false makes the first branch dead and leaves a single branch.
    expected = {
        True: [(cpre.FindingKind.REDUNDANT_BRANCH, 1), (cpre.FindingKind.DEAD_BRANCH, 3)],
        False: [(cpre.FindingKind.DEAD_BRANCH, 1)],
    }
    for value, findings in expected.items():
        result = cpre.analyze_source(source, assumptions={"A": value})
        assert [(item.kind, item.location.line) for item in result.findings] == findings


# --- The rewrite keeps the meaning -----------------------------------------------------------

EQUIVALENT = [
    ("#if A\nx;\n#elif B\nx;\n#endif\n", ("A", "B")),
    ("#if A\nx;\n#elif B\nx;\n#elif C\nx;\n#else\ny;\n#endif\n", ("A", "B", "C")),
    ("#ifdef A\nx;\n#elifndef B\nx;\n#else\ny;\n#endif\n", ("A", "B")),
    ("#if A\nx;\n#elif B\nx;\n#elif C\nx;\n#endif\n", ("A", "B", "C")),
    ("#if A\n#define B 1\n#elif B\n#define B 1\n#endif\nz = B;\n", ("A", "B")),
    ("#if A\nx;\n#elif defined(B) && !A\nx;\n#else\ny;\n#endif\n", ("A", "B")),
    ("#if A > 0\nx;\n#elif B\nx;\n#endif\n", ("A", "B")),
]


@pytest.mark.parametrize(("source", "names"), EQUIVALENT)
def test_applying_the_edit_does_not_change_the_preprocessed_output(source, names):
    assert outputs(source, names) == outputs(rewritten(source, KIND), names)


def test_reanalysing_the_rewritten_source_reports_no_join():
    source = "#if A\nx;\n#elif B\nx;\n#elif C\nx;\n#endif\n"

    after = cpre.analyze_source(rewritten(source, KIND))

    assert all(finding.kind is not KIND for finding in after.findings)


# --- Tree, counts and rendering --------------------------------------------------------------


def test_the_tree_records_every_joined_branch_line_on_the_group():
    result = cpre.analyze_source("#if A\nx;\n#elif B\nx;\n#elif C\nx;\n#endif\n")

    (group,) = result.tree.groups
    assert [(item.kind, item.lines) for item in group.suggestions] == [
        ("joinable_branches", (1, 3, 5))
    ]


def test_summary_line_and_counts():
    assert summary_line(FindingCounts(joinable=1)) == "Summary  1 joinable branch run"
    assert summary_line(FindingCounts(joinable=2)) == "Summary  2 joinable branch runs"
    assert FindingCounts(joinable=1).failing == 1
    assert (FindingCounts(joinable=1) + FindingCounts(complementary=1)).total == 2


# --- Command line ----------------------------------------------------------------------------

SOURCE = "#if A\nreturn 1;\n#elif B\nreturn 1;\n#elif C\nreturn 2;\n#endif\n"


def test_text_report_shows_a_join_badge_reason_and_summary(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    assert "JOIN" in out
    assert "the branches at lines 1 and 3 have the same body; they can be joined" in out
    assert "Summary  1 joinable branch run" in out.splitlines()


def test_the_join_badge_is_colored_like_the_merge_badge(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    main(["--color", "always", str(source)])

    assert "\033[1;33mJOIN\033[0m" in capsys.readouterr().out


def test_fail_on_findings_fails_for_a_join(tmp_path):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--fail-on-findings", str(source)]) == 1


def test_json_lists_the_suggestion_on_the_group(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--json", "--verbose", str(source)]) == 0
    (group,) = json.loads(capsys.readouterr().out)["groups"]

    assert group["suggestions"] == [{"kind": "joinable_branches", "lines": [1, 3]}]


def test_sarif_has_a_rule_related_locations_and_a_fix():
    analysis = cpre.analyze_source(SOURCE, filename="a.c")
    (result,) = sarif_log([analysis], tool_version="1")["runs"][0]["results"]

    assert result["ruleId"] == "CPRE007"
    assert result["level"] == "warning"
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    (related,) = result["relatedLocations"]
    assert related["physicalLocation"]["region"] == {"startLine": 3}
    (fix,) = result["fixes"]
    assert (
        fix["description"]["text"] == "Join the branches starting at line 3 into this one with ||"
    )
    (replacement,) = fix["artifactChanges"][0]["replacements"]
    assert replacement["insertedContent"]["text"] == "#if A || B\nreturn 1;\n"
    assert replacement["deletedRegion"] == {
        "startLine": 1,
        "endLine": 5,
        "startColumn": 1,
        "endColumn": 1,
    }


# --- Conditions a join must not repeat, move or reorder --------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # A name built by token pasting could form __LINE__ or __COUNTER__.
        "#define P(a, b) a##b\n#if A\nx;\n#elif P(__LI, NE__)\nx;\n#endif\n",
        "#define P(a, b) a##b\n#define Q P(__COUN, TER__)\n#if A\nx;\n#elif Q\nx;\n#endif\n",
        # A later condition that expands to _Pragma would run even when an earlier one is true.
        '#define PR _Pragma("once")\n#define M PR\n#if A\nx;\n#elif M\nx;\n#endif\n',
        '#if A\nx;\n#elif _Pragma("once") 1\nx;\n#endif\n',
        # A condition after the run depends on its line number, which the join moves.
        "#if A\nx;\n#elif B\nx;\n#elif __LINE__ > 100\ny;\n#endif\n",
        "#if A\nx;\n#elif B\nx;\n#endif\n#if __LINE__ > 100\n#endif\n",
        "#define N __LINE__\n#if A\nx;\n#elif B\nx;\n#elif N > 100\ny;\n#endif\n",
    ],
)
def test_conditions_that_a_join_would_repeat_or_move_are_not_reported(source):
    assert _joins(source) == []


def test_a_line_test_before_the_run_does_not_matter():
    assert len(_joins("#if __LINE__ > 100\n#endif\n#if A\nx;\n#elif B\nx;\n#endif\n")) == 1


# --- Where the badge is shown ----------------------------------------------------------------


def test_a_run_that_starts_at_an_elif_is_shown_at_that_elif(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text("#if A\nw;\n#elif B\nx;\n#elif C\nx;\n#endif\n")

    assert main(["--color", "never", str(source)]) == 0
    lines = capsys.readouterr().out.splitlines()

    badge = [line for line in lines if "JOIN" in line]
    assert len(badge) == 1
    assert badge[0].lstrip().startswith("L3")
    assert not any(line.lstrip().startswith("L1") for line in lines)
    assert any("the branches at lines 3 and 5 have the same body" in line for line in lines)


def test_two_runs_in_one_block_are_each_shown_at_their_own_first_branch(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text("#if A\nx;\n#elif B\nx;\n#elif C\ny;\n#elif D\ny;\n#endif\n")

    main(["--color", "never", str(source)])
    badges = [line.split()[0] for line in capsys.readouterr().out.splitlines() if "JOIN" in line]

    assert badges == ["L1", "L5"]


def test_a_run_that_rests_on_telling_defined_from_value_apart_needs_no_assumptions():
    # Without assumptions ``defined(A)`` and ``A`` are one test, so the nested ``#if A`` is
    # redundant and there is no run. An unrelated assumption must not make one appear.
    source = "#ifdef A\n#if A\nx;\n#elif B\nx;\n#endif\n#endif\n"

    assert _joins(source) == []
    assert _joins(source, assumptions={"C": True}) == []


def test_a_later_branch_calling_an_unknown_macro_is_not_joined():
    # ``#elif FOO(1)`` was skipped whenever ``A`` was true; ``A || (FOO(1))`` would not preprocess.
    assert _joins("#if A\nx;\n#elif FOO(1)\nx;\n#endif\n") == []


def test_calls_to_macros_of_the_file_defined_and_has_operators_are_joined():
    assert len(_joins("#define FOO(a) a\n#if A\nx;\n#elif FOO(1)\nx;\n#endif\n")) == 1
    assert (
        len(_joins("#if A\nx;\n#elif defined(B)\nx;\n#elif __has_include(<a.h>)\nx;\n#endif\n"))
        == 1
    )


def test_the_first_branch_of_a_run_may_call_an_unknown_macro():
    assert len(_joins("#if FOO(1)\nx;\n#elif A\nx;\n#endif\n")) == 1


def test_a_run_after_a_dead_opener_is_still_reported_under_an_unrelated_assumption():
    source = "#if 0\nz;\n#elif B\ny;\n#elif C\ny;\n#endif\n"

    assert len(_joins(source)) == 1
    assert len(_joins(source, assumptions={"Z": True})) == 1


@pytest.mark.parametrize(
    "later",
    [
        "B C",
        "B ? C",
        "B ? C : D :",
        "defined",
        "FOO(,)",
        "1 +",
        "B 1",
        "1 FOO(2)",
    ],
)
def test_a_later_branch_that_is_not_a_well_formed_expression_is_not_joined(later):
    # Skipped when ``A`` is true, the text is harmless; read inside ``A || ...`` it is an error.
    assert _joins(f"#define FOO(a) a\n#if A\nx;\n#elif {later}\nx;\n#endif\n") == []


@pytest.mark.parametrize(
    "later",
    [
        "B && !C",
        "B ? C : D",
        "defined B",
        "defined(B) && -1 < 2",
        "(B + 1) * 2 >= 3",
        "FOO(1, B) && C",
        "FOO()",
        "'a' == 97",
        "0x10 > 1u",
        "__has_include(<a/b.h>)",
    ],
)
def test_a_later_branch_that_is_a_well_formed_expression_is_joined(later):
    assert len(_joins(f"#define FOO(a) a\n#if A\nx;\n#elif {later}\nx;\n#endif\n")) == 1


def test_the_first_branch_of_a_run_may_be_malformed_as_it_is_read_either_way():
    assert len(_joins("#if A\nx;\n#elif B\nx;\n#endif\n")) == 1


def test_bodies_that_differ_only_in_their_line_endings_are_joined():
    source = "#if A\r\nx;\r\n#elif B\nx;\n#endif\r\n"
    result = _joins(source)

    assert len(result) == 1
    assert apply_edit(source, result[0].edit).startswith("#if A || B\r\nx;\r\n")
