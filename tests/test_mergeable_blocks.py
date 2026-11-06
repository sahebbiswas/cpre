"""Adjacent sibling blocks that test one condition are reported as mergeable (issue #239)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

import cpre
from cpre.cli import main
from cpre.reporting import FindingCounts
from cpre.sarif import sarif_log
from cpre.text_reports import summary_line

MERGEABLE = cpre.FindingKind.MERGEABLE_BLOCKS


def _merges(source: str, **kwargs) -> list[cpre.Finding]:
    result = cpre.analyze_source(source, **kwargs)
    assert result.complete
    return [finding for finding in result.findings if finding.kind is MERGEABLE]


def _lines(finding: cpre.Finding) -> tuple[int, tuple[int, ...]]:
    return finding.location.line, tuple(item.line for item in finding.related_locations)


# --- What is reported ------------------------------------------------------------------------


def test_two_blocks_with_blank_lines_between_are_one_finding():
    source = "#if FLAG_A\nint x;\n#endif\n\n\n#if FLAG_A\nint y;\n#endif\n"

    (finding,) = _merges(source)

    assert _lines(finding) == (1, (6,))
    assert finding.directive == "if"
    assert finding.original_condition == "FLAG_A"
    assert finding.exact_simplification is None
    assert finding.contextual_simplification is None
    assert finding.depends_on_assumptions is False
    assert "line 6" in finding.reason
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert finding.edit.replacement == ""
    assert (finding.edit.range.start.line, finding.edit.range.start.column) == (3, 1)
    assert (finding.edit.range.end.line, finding.edit.range.end.column) == (7, 1)


def test_blocks_that_touch_with_no_blank_line_are_reported():
    (finding,) = _merges("#if A\nx;\n#endif\n#if A\ny;\n#endif\n")
    assert _lines(finding) == (1, (4,))


def test_empty_bodies_are_reported():
    (finding,) = _merges("#if A\n#endif\n#if A\n#endif\n")
    assert _lines(finding) == (1, (3,))


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("#ifdef A", "#ifdef A"),
        ("#ifndef A", "#ifndef A"),
        ("#ifdef A", "#if defined(A)"),
        ("#if defined A", "#ifdef A"),
        ("#ifndef A", "#if !defined(A)"),
        ("#if A && B", "#if B && A"),
        ("#if A || B", "#if B || A"),
        ("#if (A)", "#if A"),
        ("#if A && (B || C)", "#if (C || B) && A"),
        ("#if A", "#if A && A"),
        ("#if !(A || B)", "#if !A && !B"),
    ],
)
def test_equivalent_conditions_are_reported(first, second):
    source = f"{first}\nx;\n#endif\n{second}\ny;\n#endif\n"
    (finding,) = _merges(source)
    assert _lines(finding) == (1, (4,))


def test_a_run_of_three_is_one_finding_listing_both_later_blocks():
    source = "#if A\nx;\n#endif\n\n#if A\ny;\n#endif\n\n#if A\nz;\n#endif\n"

    (finding,) = _merges(source)

    assert _lines(finding) == (1, (5, 9))
    assert "lines 5, 9" in finding.reason


def test_a_run_stops_at_a_block_with_another_condition():
    source = "#if A\n#endif\n#if A\n#endif\n#if B\n#endif\n#if B\n#endif\n#if A\n#endif\n"

    findings = _merges(source)

    assert [_lines(finding) for finding in findings] == [(1, (3,)), (5, (7,))]


def test_each_condition_group_in_a_file_is_reported_separately():
    source = "#if A\n#endif\n#if A\n#endif\nint x;\n#if A\n#endif\n#if A\n#endif\n"

    findings = _merges(source)

    assert [_lines(finding) for finding in findings] == [(1, (3,)), (6, (8,))]


def test_nested_blocks_are_reported_inside_their_parent_branch():
    source = (
        "#if P\n"
        "#if A\nx;\n#endif\n"
        "\n"
        "#if A\ny;\n#endif\n"
        "#else\n"
        "#if B\n#endif\n#if B\n#endif\n"
        "#endif\n"
    )

    findings = _merges(source)

    assert [_lines(finding) for finding in findings] == [(2, (6,)), (10, (12,))]


def test_siblings_in_different_branches_of_a_parent_are_not_merged():
    source = "#if P\n#if A\n#endif\n#else\n#if A\n#endif\n#endif\n"
    assert _merges(source) == []


def test_findings_are_in_source_order_with_other_findings():
    source = "#if A\n#endif\n#if A\n#endif\n#if B\n#if B\n#endif\n#endif\n"

    result = cpre.analyze_source(source)

    assert [(finding.kind, finding.location.line) for finding in result.findings] == [
        (MERGEABLE, 1),
        (cpre.FindingKind.REDUNDANT_BRANCH, 6),
    ]


def test_crlf_and_form_feed_gaps_count_as_blank():
    (finding,) = _merges("#if A\r\nx;\r\n#endif\r\n\f\r\n  \t\r\n#if A\r\ny;\r\n#endif\r\n")
    assert _lines(finding) == (1, (6,))


def test_directives_may_be_indented_and_spaced():
    (finding,) = _merges("  #  if A\nx;\n  #  endif\n\t#if A\ny;\n#endif\n")
    assert _lines(finding) == (1, (4,))


def test_a_body_with_unrelated_defines_is_still_reported():
    source = "#if A\n#define B 1\n#undef C\n#endif\n#if A\nx;\n#endif\n"
    (finding,) = _merges(source)
    assert _lines(finding) == (1, (5,))


def test_comments_on_the_first_opener_and_the_last_endif_are_kept_by_a_merge():
    source = "#if A // first\nx;\n#endif\n#if A\ny;\n#endif // A\n"
    (finding,) = _merges(source)
    assert _lines(finding) == (1, (4,))


def test_a_comment_inside_a_body_does_not_matter():
    source = "#if A\n// note\nx; /* more */\n#endif\n#if A\ny;\n#endif\n"
    (finding,) = _merges(source)
    assert _lines(finding) == (1, (5,))


def test_assumptions_can_only_remove_a_finding_never_add_or_change_one():
    source = "#if A\n#endif\n#if A\n#endif\n"

    plain = _merges(source)
    unrelated = _merges(source, assumptions={"B": True})

    assert [_lines(f) for f in plain] == [_lines(f) for f in unrelated] == [(1, (3,))]
    assert unrelated[0].depends_on_assumptions is False


def test_blocks_that_assumptions_make_dead_or_redundant_are_reported_as_such():
    source = "#if A\n#endif\n#if A\n#endif\n"

    for value, kind in (
        (True, cpre.FindingKind.REDUNDANT_BRANCH),
        (False, cpre.FindingKind.DEAD_BRANCH),
    ):
        result = cpre.analyze_source(source, assumptions={"A": value})
        assert {finding.kind for finding in result.findings} == {kind}
        assert all(group.merge_with == () for group in result.tree.groups)


# --- What is not reported --------------------------------------------------------------------


@pytest.mark.parametrize(
    "gap",
    [
        "// a note\n",
        "/* a note */\n",
        "/* a\nnote */\n",
        "\n// a note\n\n",
        "// #define A 1\n",
        "int between;\n",
        ";\n",
        "#define OTHER 1\n",
        "#line 100\n",
        "#pragma once\n",
        "#error stop\n",
        " \n",
    ],
)
def test_anything_but_blank_lines_between_the_blocks_prevents_a_merge(gap):
    source = f"#if A\nx;\n#endif\n{gap}#if A\ny;\n#endif\n"
    assert _merges(source) == []


@pytest.mark.parametrize(
    "source",
    [
        # A comment on the #endif or on the next #if would be lost by a merge.
        "#if A\n#endif // A\n#if A\n#endif\n",
        "#if A\n#endif /* A */\n#if A\n#endif\n",
        "#if A\n#endif\n#if A // again\n#endif\n",
        "#if A\n#endif\n/* again */ #if A\n#endif\n",
        "#if A\n#endif\n#if /* again */ A\n#endif\n",
        "#if A\n#endif /* spans\nlines */\n#if A\n#endif\n",
    ],
)
def test_a_comment_on_the_junction_directives_prevents_a_merge(source):
    assert _merges(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "#if A\n#endif\n#if B\n#endif\n",
        "#if A\n#endif\n#if !A\n#endif\n",
        "#if A\n#endif\n#if A && B\n#endif\n",
        "#if A || B\n#endif\n#if A\n#endif\n",
        # ``A`` (a value) and ``defined(A)`` are different tests.
        "#if A\n#endif\n#ifdef A\n#endif\n",
        "#if A\n#endif\n#if defined(A)\n#endif\n",
        "#ifdef A\n#endif\n#ifndef A\n#endif\n",
    ],
)
def test_different_conditions_are_not_merged(source):
    assert _merges(source) == []


@pytest.mark.parametrize(
    "condition",
    [
        "A > 1",
        "A == B",
        "FOO(1)",
        "A ? B : C",
        "__has_include(<x.h>)",
        "__LINE__",
        "__COUNTER__",
        "A && __COUNTER__",
        "A + 1",
    ],
)
def test_opaque_or_volatile_conditions_are_not_merged(condition):
    source = f"#if {condition}\nx;\n#endif\n#if {condition}\ny;\n#endif\n"
    assert _merges(source) == []


@pytest.mark.parametrize(
    "source",
    [
        # The first block's body changes a name the condition uses.
        "#if A\n#undef A\n#endif\n#if A\n#endif\n",
        "#if A\n#define A 0\n#endif\n#if A\n#endif\n",
        "#if A && B\n#undef B\n#endif\n#if B && A\n#endif\n",
        "#ifdef A\n#undef A\n#endif\n#ifdef A\n#endif\n",
        "#if A\n#  undef   A\n#endif\n#if A\n#endif\n",
        "#if A\n#define A(x) x\n#endif\n#if A\n#endif\n",
        # ...also from inside a nested block of the body.
        "#if A\n#if B\n#undef A\n#endif\n#endif\n#if A\n#endif\n",
        # Headers and pragmas may change any macro.
        "#if A\n#include <x.h>\n#endif\n#if A\n#endif\n",
        '#if A\n#include "x.h"\n#endif\n#if A\n#endif\n',
        "#if A\n#include_next <x.h>\n#endif\n#if A\n#endif\n",
        "#if A\n#import <x.h>\n#endif\n#if A\n#endif\n",
        '#if A\n#pragma pop_macro("A")\n#endif\n#if A\n#endif\n',
        "#if A\n#pragma once\n#endif\n#if A\n#endif\n",
        # A malformed definition cannot be shown to leave the condition alone.
        "#if A\n#define\n#endif\n#if A\n#endif\n",
    ],
)
def test_a_body_that_can_change_the_condition_prevents_a_merge(source):
    assert _merges(source) == []


def test_only_the_first_block_body_matters_for_a_pair():
    # The second block may change A: nothing is tested after it.
    (finding,) = _merges("#if A\nx;\n#endif\n#if A\n#undef A\n#endif\n")
    assert _lines(finding) == (1, (4,))


def test_a_run_breaks_where_a_middle_block_changes_the_condition():
    source = "#if A\n#endif\n#if A\n#undef A\n#endif\n#if A\n#endif\n"
    (finding,) = _merges(source)
    assert _lines(finding) == (1, (3,))


@pytest.mark.parametrize(
    "source",
    [
        # #else or #elif on either block.
        "#if A\n#else\n#endif\n#if A\n#endif\n",
        "#if A\n#endif\n#if A\n#else\n#endif\n",
        "#if A\n#elif B\n#endif\n#if A\n#endif\n",
        "#if A\n#endif\n#if A\n#elif B\n#endif\n",
        # ...including the case from the issue, which is not a simple merge.
        "#if A\nx;\n#elif B\ny;\n#endif\n#if B\nz;\n#endif\n",
        "#ifdef A\n#elifdef B\n#endif\n#ifdef A\n#endif\n",
    ],
)
def test_blocks_with_else_or_elif_are_not_merged(source):
    assert _merges(source) == []


@pytest.mark.parametrize(
    "source",
    [
        # Dead and redundant blocks are reported as such, not as mergeable.
        "#if 0\n#endif\n#if 0\n#endif\n",
        "#if 1\n#endif\n#if 1\n#endif\n",
        "#if P\n#if P\n#endif\n#if P\n#endif\n#endif\n",
        "#if P\n#else\n#if P\n#endif\n#if P\n#endif\n#endif\n",
    ],
)
def test_dead_and_redundant_blocks_are_not_reported_as_mergeable(source):
    assert _merges(source) == []


def test_unmatched_sibling_nesting_does_not_pair_with_the_next_parent_block():
    source = "#if P\n#if A\n#endif\n#endif\n#if A\n#endif\n"
    assert _merges(source) == []


# --- Limits and incomplete analysis ----------------------------------------------------------


def test_blocks_of_an_unanalyzed_component_are_never_reported():
    source = "#if A && B\n#endif\n#if A && B\n#endif\n#if C\n#endif\n#if C\n#endif\n"

    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=1))
    merged = [finding for finding in result.findings if finding.kind is MERGEABLE]

    assert not result.complete
    assert [_lines(finding) for finding in merged] == [(5, (7,))]
    assert all(group.merge_with == () for group in result.tree.groups[:2])


def _work(source: str, **options) -> int:
    result = cpre.analyze_source(source, telemetry=True, **options)
    assert result.complete and result.telemetry is not None
    return result.telemetry.work


def test_a_proof_that_exhausts_the_work_budget_curtails_the_whole_result():
    # A comment between the blocks rules the pair out before any proof, so its work is
    # exactly what the ordinary analysis needs. The same budget must then be too small for
    # the proof that the uncommented pair needs on top of it.
    pair = "#if A && B\n#endif\n#if B && A\n#endif\n"
    unproven = "#if A && B\n#endif\n// c\n#if B && A\n#endif\n"
    needed = _work(unproven)

    assert _lines(_merges(pair, options=cpre.AnalysisOptions(max_work=needed + 100_000))[0]) == (
        1,
        (3,),
    )
    starved = cpre.analyze_source(pair, options=cpre.AnalysisOptions(max_work=needed))

    assert not starved.complete
    assert starved.findings == ()
    assert starved.incomplete[0].resource == "work"


def test_the_proof_counts_toward_the_shared_work_budget():
    pair = "#if A && B\n#endif\n#if B && A\n#endif\n"
    unproven = "#if A && B\n#endif\n// c\n#if B && A\n#endif\n"

    assert _work(pair) > _work(unproven)


def test_a_pair_whose_proof_exceeds_a_node_limit_is_skipped_not_reported():
    # Each condition fits the node limit on its own as an expression, but the proof builds
    # both. Whatever the limit allows, no wrong finding appears.
    source = "#if A && B && C\n#endif\n#if C && B && A\n#endif\n"

    complete = _merges(source)
    limited = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_bdd_nodes=5))

    assert [_lines(f) for f in complete] == [(1, (3,))]
    assert all(f.kind is not MERGEABLE for f in limited.findings) or not limited.complete


def test_conditions_that_test_different_names_spend_no_extra_work():
    # Neither pair can reach the proof, so both cost what the ordinary analysis costs.
    apart = _work("#if A\n#endif\n#if B\n#endif\n")
    commented = _work("#if A\n#endif\n// c\n#if B\n#endif\n")
    assert apart == commented


def test_a_condition_with_an_extra_unrelated_term_is_still_compared():
    # ``B && !B`` is false, so the second condition is just ``A``; the proof runs over both
    # condition's tests, not only the shared ones.
    source = "#if A\nx;\n#endif\n#if A || (B && !B)\ny;\n#endif\n"
    assert _lines(_merges(source)[0]) == (1, (4,))


# --- Tree, counts and rendering --------------------------------------------------------------


def test_the_tree_records_the_run_on_its_first_block_only():
    result = cpre.analyze_source("#if A\n#endif\n#if A\n#endif\n#if A\n#endif\n")

    assert [group.merge_with for group in result.tree.groups] == [(3, 5), (), ()]


def test_a_source_without_runs_has_empty_merge_with_everywhere():
    result = cpre.analyze_source("#if A\n#if B\n#endif\n#endif\n#if C\n#endif\n")

    assert all(group.merge_with == () for group in result.tree.groups)


def test_summary_line_counts_runs():
    assert "1 mergeable block run" in summary_line(FindingCounts(mergeable=1))
    assert "2 mergeable block runs" in summary_line(FindingCounts(mergeable=2))
    assert FindingCounts(mergeable=1).failing == 1
    assert FindingCounts(mergeable=1).total == 1


# --- Command line ----------------------------------------------------------------------------

TWO = "#if FLAG_A\nint x;\n#endif\n\n#if FLAG_A\nint y;\n#endif\n"
THREE = TWO + "#if FLAG_A\nint z;\n#endif\n"


def _write(tmp_path: Path, text: str, name: str = "m.c") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_text_report_shows_a_merge_badge_reason_and_summary(tmp_path, capsys):
    source = _write(tmp_path, TWO)

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    lines = out.splitlines()
    assert lines[0].startswith("L1")
    assert "MERGE" in lines[0]
    assert "#if FLAG_A" in lines[0]
    assert lines[1].strip() == (
        "merge  the same condition is tested again at line 5; the blocks can be merged"
    )
    assert "Summary  1 mergeable block run" in out


def test_text_report_for_a_run_lists_every_later_block(tmp_path, capsys):
    source = _write(tmp_path, THREE)

    main(["--color", "never", str(source)])

    assert "tested again at lines 5 and 8" in capsys.readouterr().out


def test_text_report_for_a_nested_run_is_shown_under_its_parent(tmp_path, capsys):
    source = _write(tmp_path, "#if P\n#if A\n#endif\n#if A\n#endif\n#endif\n")

    main(["--color", "never", str(source)])

    out = capsys.readouterr().out
    assert "L1 ok" in out
    assert "L2 MERGE" in out


def test_merge_badge_is_colored_like_redundant(tmp_path, capsys):
    source = _write(tmp_path, TWO)

    main(["--color", "always", str(source)])

    assert "\033[1;33mMERGE\033[0m" in capsys.readouterr().out


def test_fail_on_findings_exits_1_for_a_mergeable_run(tmp_path, capsys):
    source = _write(tmp_path, TWO)

    assert main(["--fail-on-findings", str(source)]) == 1
    capsys.readouterr()
    clean = _write(tmp_path, "#if A\n#endif\n#if B\n#endif\n", "clean.c")
    assert main(["--fail-on-findings", str(clean)]) == 0


def test_a_clean_file_reports_nothing_new(tmp_path, capsys):
    source = _write(tmp_path, "#if A\nx;\n#endif\n#if B\ny;\n#endif\n")

    assert main(["--color", "never", str(source)]) == 0
    assert capsys.readouterr().out.strip() == (
        "No notable conditional directives or simplifiable macros found."
    )


def test_json_records_merge_with_on_every_group(tmp_path, capsys):
    source = _write(tmp_path, THREE + "#if B\n#endif\n")

    assert main(["--json", "--verbose", str(source)]) == 0
    document = json.loads(capsys.readouterr().out)

    assert [(g["line"], g["merge_with"]) for g in document["groups"]] == [
        (1, [5, 8]),
        (5, []),
        (8, []),
        (11, []),
    ]


def test_json_shows_a_file_with_only_a_merge_in_a_filtered_batch(tmp_path, capsys):
    one = _write(tmp_path, TWO, "one.c")
    two = _write(tmp_path, "#if A\n#endif\n", "two.c")

    assert main(["--json", str(one), str(two)]) == 0
    document = json.loads(capsys.readouterr().out)

    assert [file["path"] for file in document["files"]] == [str(one)]
    assert document["files"][0]["groups"][0]["merge_with"] == [5]


def test_sarif_has_a_rule_and_related_locations(tmp_path, capsys):
    source = _write(tmp_path, THREE)

    assert main(["--sarif", str(source)]) == 0
    run = json.loads(capsys.readouterr().out)["runs"][0]

    rules = run["tool"]["driver"]["rules"]
    (result,) = run["results"]
    assert rules[result["ruleIndex"]]["id"] == result["ruleId"] == "CPRE005"
    assert rules[result["ruleIndex"]]["name"] == "mergeable-blocks"
    assert result["level"] == "warning"
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    assert [
        (item["id"], item["physicalLocation"]["region"]["startLine"])
        for item in result["relatedLocations"]
    ] == [(0, 5), (1, 8)]
    assert result["properties"]["kind"] == "mergeable_blocks"
    assert result["properties"]["fixConfidence"] == "exact"
    assert result["message"]["text"] == (
        "the same condition is tested again at lines 5, 8; the blocks can be merged"
    )
    (fix,) = result["fixes"]
    assert fix["description"]["text"] == "Merge the blocks starting at lines 5, 8 into this one"
    (replacement,) = fix["artifactChanges"][0]["replacements"]
    assert replacement["deletedRegion"] == {
        "startLine": 3,
        "endLine": 9,
        "startColumn": 1,
        "endColumn": 1,
    }
    assert replacement["insertedContent"]["text"] == "int y;\n"


def test_sarif_results_without_related_locations_are_unchanged():
    analysis = cpre.analyze_source("#if A\n#if A\n#endif\n#endif\n", filename="x.c")

    (result,) = sarif_log([analysis], tool_version="1")["runs"][0]["results"]

    assert "relatedLocations" not in result


def test_other_findings_have_no_related_locations():
    result = cpre.analyze_source("#if A\n#if A\n#endif\n#endif\n")

    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.REDUNDANT_BRANCH]
    assert all(finding.related_locations == () for finding in result.findings)


# --- Merging keeps the preprocessed output ---------------------------------------------------


def _outputs(source: str, names: tuple[str, ...]) -> list[str]:
    """The retained text of ``source`` under every closed-world setting of ``names``."""
    settings = [None, 0, 1]
    outputs = []
    for values in itertools.product(settings, repeat=len(names)):
        configuration = cpre.MacroConfiguration(
            undefined=[name for name, value in zip(names, values, strict=True) if value is None],
            integers={
                name: value for name, value in zip(names, values, strict=True) if value is not None
            },
            unknown_names="undefined",
        )
        result = cpre.preprocess_source(source, configuration=configuration)
        assert result.complete
        assert result.source is not None
        outputs.append(" ".join(result.source.split()))
    return outputs


MERGES = [
    (
        "#if A\nint x;\n#endif\n\n#if A\nint y;\n#endif\n",
        "#if A\nint x;\n\nint y;\n#endif\n",
        ("A",),
    ),
    (
        "#ifdef A\nint x;\n#endif\n#if defined(A)\nint y;\n#endif\n",
        "#ifdef A\nint x;\nint y;\n#endif\n",
        ("A",),
    ),
    (
        "#if A && B\nint x;\n#endif\n#if B && A\nint y;\n#endif\n",
        "#if A && B\nint x;\nint y;\n#endif\n",
        ("A", "B"),
    ),
    (
        "#if A\n#define B 1\n#endif\n#if A\nint y = B;\n#endif\n",
        "#if A\n#define B 1\nint y = B;\n#endif\n",
        ("A", "B"),
    ),
]

UNSAFE = [
    (
        "#if A\n#undef A\n#endif\n#if A\nint y;\n#endif\n",
        "#if A\n#undef A\nint y;\n#endif\n",
        ("A",),
    ),
    (
        "#if A\n#define A 0\n#endif\n#if A\nint y;\n#endif\n",
        "#if A\n#define A 0\nint y;\n#endif\n",
        ("A",),
    ),
]


@pytest.mark.parametrize(("original", "merged", "names"), MERGES)
def test_merging_a_reported_pair_does_not_change_the_preprocessed_output(original, merged, names):
    assert _merges(original)
    assert _outputs(original, names) == _outputs(merged, names)


@pytest.mark.parametrize(("original", "merged", "names"), UNSAFE)
def test_the_unsafe_cases_are_not_reported_because_a_merge_would_change_the_output(
    original, merged, names
):
    assert _merges(original) == []
    assert _outputs(original, names) != _outputs(merged, names)


def test_a_long_definition_chain_is_walked_once_per_name_not_once_per_pair():
    chain = "".join(f"#define M{i} M{i + 1}\n" for i in range(300)) + "#define M300 1\n"
    blocks = "#if M0\n#endif\n" * 400
    result = cpre.analyze_source(chain + blocks)

    assert [f.kind for f in result.findings] == [MERGEABLE]
    assert len(result.findings[0].related_locations) == 399
