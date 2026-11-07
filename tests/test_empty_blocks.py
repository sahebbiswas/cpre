"""Blocks and trailing branches with no content can be deleted (issue #243)."""

from __future__ import annotations

import json

import pytest
from block_rewrite_support import apply_edit, findings_of, outputs, rewritten

import cpre
from cpre.cli import main
from cpre.reporting import FindingCounts
from cpre.sarif import sarif_log
from cpre.text_reports import summary_line

BLOCK = cpre.FindingKind.EMPTY_BLOCK
BRANCH = cpre.FindingKind.EMPTY_BRANCH


def _blocks(source: str, **kwargs) -> list[cpre.Finding]:
    return findings_of(source, BLOCK, **kwargs)


def _branches(source: str, **kwargs) -> list[cpre.Finding]:
    return findings_of(source, BRANCH, **kwargs)


def _lines(finding: cpre.Finding) -> tuple[int, tuple[int, ...]]:
    return finding.location.line, tuple(item.line for item in finding.related_locations)


# --- Empty blocks ----------------------------------------------------------------------------


def test_a_block_with_no_content_is_deleted_by_an_exact_edit():
    (finding,) = _blocks("#if A\n#endif\nint x;\n")

    assert _lines(finding) == (1, ())
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert finding.depends_on_assumptions is False
    assert rewritten("#if A\n#endif\nint x;\n", BLOCK) == "int x;\n"


@pytest.mark.parametrize(
    "source",
    [
        "#if A\n#endif\n",
        "#ifdef A\n#endif\n",
        "#ifndef A\n#endif\n",
        "#if A\n\n   \n#endif\n",
        "#if A\n#else\n#endif\n",
        "#if A\n#elif B\n#else\n#endif\n",
        "#if A\n#elif B\n#elif C\n#endif\n",
        "  #  if A\n  #  endif\n",
    ],
)
def test_every_branch_without_content_makes_the_block_empty(source):
    (finding,) = _blocks(source)

    assert rewritten(source, BLOCK) == ""
    assert _branches(source) == []
    assert finding.kind is BLOCK


def test_blank_lines_around_the_block_stay():
    assert rewritten("a;\n\n#if A\n#endif\n\nb;\n", BLOCK) == "a;\n\n\nb;\n"


def test_a_block_holding_only_empty_blocks_is_one_finding_naming_them():
    source = "#if A\n#if B\n#endif\n\n#ifdef C\n#else\n#endif\n#endif\n"

    (finding,) = _blocks(source)

    assert _lines(finding) == (1, (2, 5))
    assert rewritten(source, BLOCK) == ""
    # The nested blocks are not reported again, nor offered as a collapse.
    assert len(cpre.analyze_source(source).findings) == 1


def test_a_nested_block_with_content_keeps_the_outer_block():
    source = "#if A\n#if B\n#endif\nx;\n#endif\n"

    assert [_lines(f) for f in _blocks(source)] == [(2, ())]


def test_nested_empty_blocks_inside_an_empty_branch_of_a_block_with_content_are_reported_alone():
    source = "#if A\nx;\n#else\n#if B\n#endif\n#endif\n"

    assert [_lines(f) for f in _blocks(source)] == [(4, ())]
    assert [_lines(f) for f in _branches(source)] == []


def test_only_the_outermost_empty_block_of_siblings_is_not_special_each_is_reported():
    assert [_lines(f) for f in _blocks("#if A\n#endif\n#if B\n#endif\n")] == [(1, ()), (3, ())]


# --- Blocks that are not reported ------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "#if A\n/* keep */\n#endif\n",
        "#if A\n// keep\n#endif\n",
        "#if A // why\n#endif\n",
        "#if A\n#endif // end A\n",
        "#if A\n#else // other\n#endif\n",
        "#ifndef X\n#define X\n#endif\n",
        "#if A\n#error no\n#endif\n",
        "#if A\n#warning w\n#endif\n",
        "#if A\n#pragma once\n#endif\n",
        "#if A\n#include <a.h>\n#endif\n",
        "#if A\n#undef B\n#endif\n",
        "#if A\n#line 5\n#endif\n",
        "#if A\n;\n#endif\n",
        "#if A\n#if B\n/* c */\n#endif\n#endif\n",
    ],
)
def test_content_and_comments_keep_a_block(source):
    assert _blocks(source) == []
    assert _branches(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "#if __COUNTER__\n#endif\n",
        "#if __LINE__ > 3\n#endif\n",
        "#define C __COUNTER__\n#if C\n#endif\n",
        "#define P(a, b) a##b\n#if P(__COUN, TER__)\n#endif\n",
    ],
)
def test_a_condition_that_is_not_inert_keeps_the_block(source):
    assert _blocks(source) == []


def test_a_later_line_test_keeps_the_block():
    assert _blocks("#if A\n#endif\n#if __LINE__ > 2\nx;\n#endif\n") == []


def test_a_dead_block_is_reported_as_dead_not_as_empty():
    result = cpre.analyze_source("#if 0\n#endif\n")

    assert [f.kind for f in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


def test_a_block_with_a_dead_branch_is_left_to_that_finding():
    result = cpre.analyze_source("#if A\n#elif A\n#endif\n")

    assert BLOCK not in {f.kind for f in result.findings}
    assert cpre.FindingKind.DEAD_BRANCH in {f.kind for f in result.findings}


# --- Empty trailing branches -----------------------------------------------------------------


def test_an_empty_else_is_dropped():
    source = "#if A\nint x;\n#else\n#endif\n"
    (finding,) = _branches(source)

    assert _lines(finding) == (3, ())
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert rewritten(source, BRANCH) == "#if A\nint x;\n#endif\n"


def test_an_empty_last_elif_is_dropped():
    assert rewritten("#if A\nx;\n#elif B\ny;\n#elif C\n#endif\n", BRANCH) == (
        "#if A\nx;\n#elif B\ny;\n#endif\n"
    )


def test_empty_branches_at_the_end_are_one_finding_naming_each():
    source = "#if A\nx;\n#elif B\n\n#elif C\n#else\n#endif\n"
    (finding,) = _branches(source)

    assert _lines(finding) == (3, (5, 6))
    assert rewritten(source, BRANCH) == "#if A\nx;\n#endif\n"


def test_an_empty_branch_in_the_middle_is_kept():
    # When B is true the empty branch ends the chain; dropping it would let C run instead.
    assert _branches("#if A\nx;\n#elif B\n#elif C\ny;\n#endif\n") == []


def test_dropping_the_middle_branch_would_change_the_output():
    kept = "#if A\nx;\n#elif B\n#elif C\ny;\n#endif\n"
    dropped = "#if A\nx;\n#elif C\ny;\n#endif\n"

    assert outputs(kept, ("A", "B", "C")) != outputs(dropped, ("A", "B", "C"))


def test_an_empty_branch_before_a_non_empty_else_is_kept():
    assert _branches("#if A\nx;\n#elif B\n#else\ny;\n#endif\n") == []


def test_an_empty_first_branch_is_not_dropped():
    assert _branches("#if A\n#else\nx;\n#endif\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "#if A\nx;\n#else // none\n#endif\n",
        "#if A\nx;\n#elif B // none\n#endif\n",
        "#if A\nx;\n#else\n/* none */\n#endif\n",
        "#if A\nx;\n#elif __COUNTER__\n#endif\n",
        "#if A\nx;\n#elif B\n#define Z 1\n#endif\n",
    ],
)
def test_comments_content_and_counters_keep_a_trailing_branch(source):
    assert _branches(source) == []


def test_a_branch_the_analysis_already_reports_is_not_reported_again():
    result = cpre.analyze_source("#if A\nx;\n#elif !A\n#elif A\n#endif\n")

    assert BRANCH not in {f.kind for f in result.findings}


def test_empty_branches_do_not_also_offer_to_join_them():
    result = cpre.analyze_source("#if A\nx;\n#elif B\n#elif C\n#endif\n")

    assert [f.kind for f in result.findings] == [BRANCH]


def test_a_join_elsewhere_in_the_block_is_still_offered():
    result = cpre.analyze_source("#if A\nx;\n#elif B\nx;\n#elif C\n#endif\n")

    assert {f.kind for f in result.findings} == {cpre.FindingKind.JOINABLE_BRANCHES, BRANCH}


# --- The edit leaves the program as it was ---------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "#if A\n#endif\nx;\n",
        "#if A\nx;\n#else\n#endif\n",
        "#if A\nx;\n#elif B\n#endif\n",
        "#if A\nx;\n#elif B\ny;\n#elif C\n#else\n#endif\n",
        "#if A\n#if B\n#endif\n#endif\ny;\n",
        "#if A\n#define B 1\n#else\n#endif\n#if B\nz;\n#endif\n",
        "#ifdef A\n#elif defined(B)\n#else\n#endif\nq;\n",
    ],
)
def test_applying_the_edit_does_not_change_the_preprocessed_output(source):
    findings = [f for f in cpre.analyze_source(source).findings if f.kind in {BLOCK, BRANCH}]
    assert findings

    for finding in findings:
        assert finding.edit is not None
        after = apply_edit(source, finding.edit)
        assert outputs(after, ("A", "B", "C")) == outputs(source, ("A", "B", "C"))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#if A\r\n#endif\r\nx;\r\n", "x;\r\n"),
        ("#if A\r\nx;\r\n#else\r\n#endif\r\n", "#if A\r\nx;\r\n#endif\r\n"),
        ("#if A\rx;\r#else\r#endif\r", "#if A\rx;\r#endif\r"),
        ("x;\n#if A\n#endif", "x;\n"),
        ("#if A\nx;\n#else\n#endif", "#if A\nx;\n#endif"),
    ],
)
def test_line_endings_and_a_missing_final_newline_are_kept(source, expected):
    (finding,) = [f for f in cpre.analyze_source(source).findings if f.kind in {BLOCK, BRANCH}]

    assert finding.edit is not None
    assert apply_edit(source, finding.edit) == expected


# --- Assumptions -----------------------------------------------------------------------------


def test_assumptions_can_only_remove_a_finding():
    source = "#if A\nx;\n#else\n#endif\n#if B\n#endif\n"

    assert len(_branches(source)) == len(_branches(source, assumptions={"Z": True})) == 1
    assert len(_blocks(source)) == len(_blocks(source, assumptions={"Z": True})) == 1
    assert all(f.depends_on_assumptions is False for f in _blocks(source, assumptions={"Z": True}))
    assert _blocks(source, assumptions={"B": False}) == []


def test_an_empty_branch_after_a_dead_opener_is_still_reported_under_an_assumption():
    source = "#if 0\nz;\n#elif B\ny;\n#else\n#endif\n"

    assert len(_branches(source)) == 1
    assert len(_branches(source, assumptions={"Z": True})) == 1


# --- Reports ---------------------------------------------------------------------------------


def test_the_tree_records_the_suggestions():
    result = cpre.analyze_source("#if A\n#endif\n#if B\nx;\n#else\n#endif\n")

    block, branch = result.tree.groups
    assert [(item.kind, item.lines) for item in block.suggestions] == [("empty_block", ())]
    assert [(item.kind, item.lines) for item in branch.suggestions] == [("empty_branch", (5,))]


def test_empty_findings_count_and_fail():
    counts = FindingCounts(empty_blocks=2, empty_branches=1)

    assert counts.failing == 3
    assert counts.total == 3
    assert summary_line(counts) == "Summary  2 empty blocks, 1 empty branch run"
    assert summary_line(FindingCounts(empty_blocks=1)) == "Summary  1 empty block"


def test_text_report_shows_a_badge_a_reason_and_a_summary(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text("#if A\n#endif\n#if B\nx;\n#elif C\n#else\n#endif\n")

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    assert "EMPTY" in out
    assert "this block has no content; it can be deleted" in out
    assert "the empty branches at lines 5 and 6 end the block; they can be deleted" in out
    assert "Summary  1 empty block · 1 empty branch run" in out.splitlines()


def test_a_branch_finding_is_shown_at_the_first_empty_branch(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text("#if B\nx;\n#else\n#endif\n")

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    assert out.startswith("L3 EMPTY")
    assert "the empty branch at line 3 ends the block; it can be deleted" in out


def test_fail_on_findings_exits_1_for_an_empty_block_or_branch(tmp_path):
    block = tmp_path / "a.c"
    block.write_text("#if A\n#endif\n")
    branch = tmp_path / "b.c"
    branch.write_text("#if A\nx;\n#else\n#endif\n")

    assert main(["--fail-on-findings", str(block)]) == 1
    assert main(["--fail-on-findings", str(branch)]) == 1


def test_json_lists_the_suggestions_on_the_group(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text("#if A\n#if B\n#endif\n#endif\n#if C\nx;\n#else\n#endif\n")

    assert main(["--json", "--verbose", str(source)]) == 0
    outer, other = json.loads(capsys.readouterr().out)["groups"]

    assert outer["suggestions"] == [{"kind": "empty_block", "lines": [2]}]
    assert other["suggestions"] == [{"kind": "empty_branch", "lines": [7]}]


def test_sarif_has_warning_rules_and_fixes():
    analysis = cpre.analyze_source(
        "#if A\n#if B\n#endif\n#endif\n#if C\nx;\n#else\n#endif\n", filename="a.c"
    )
    block, branch = sarif_log([analysis], tool_version="1")["runs"][0]["results"]

    assert (block["ruleId"], block["level"]) == ("CPRE009", "warning")
    assert block["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    assert [r["physicalLocation"]["region"] for r in block["relatedLocations"]] == [
        {"startLine": 2}
    ]
    assert block["fixes"][0]["description"]["text"] == "Delete the empty block"
    (replacement,) = block["fixes"][0]["artifactChanges"][0]["replacements"]
    assert replacement["insertedContent"]["text"] == ""

    assert (branch["ruleId"], branch["level"]) == ("CPRE010", "warning")
    assert branch["locations"][0]["physicalLocation"]["region"] == {"startLine": 7}
    assert "relatedLocations" not in branch
    assert branch["fixes"][0]["description"]["text"] == "Delete the empty trailing branches"


def test_the_empty_rules_are_in_the_rule_table():
    rules = sarif_log([], tool_version="1")["runs"][0]["tool"]["driver"]["rules"]

    assert [rule["id"] for rule in rules][-2:] == ["CPRE009", "CPRE010"]


# --- Review follow-ups -----------------------------------------------------------------------


def test_an_empty_block_inside_a_commented_collapsible_chain_is_not_reported_twice():
    source = "#if A\n#if B\n#endif\n#endif // A\n"
    findings = [
        finding
        for finding in cpre.analyze_source(source).findings
        if finding.kind in {BLOCK, BRANCH, cpre.FindingKind.COLLAPSIBLE_NESTING}
    ]

    spans = [
        (finding.edit.range.start.line, finding.edit.range.end.line)
        for finding in findings
        if finding.edit is not None
    ]
    for index, (start, end) in enumerate(spans):
        for other_start, other_end in spans[index + 1 :]:
            assert end <= other_start or other_end <= start, spans


@pytest.mark.parametrize(
    "source",
    [
        "#if A\nx\n#elif 1/0\n#endif\n",
        "#if A\nx\n#elif defined\n#endif\n",
        "#if 1/0\n#endif\n",
        "#if A\nx\n#elif UNKNOWN(1)\n#endif\n",
    ],
)
def test_a_condition_that_may_fail_to_evaluate_is_never_deleted(source):
    assert _blocks(source) == []
    assert _branches(source) == []


def test_a_disqualified_branch_only_shrinks_the_trailing_run():
    source = "#if A\nx\n#elif B\n#else // note\n#endif\n"

    (finding,) = _branches(source)

    assert finding.location.line == 3
    assert rewritten(source, BRANCH) == "#if A\nx\n#else // note\n#endif\n"


def test_an_empty_block_needs_every_branch_reachable_without_the_assumptions():
    source = "#if X\n#elif defined(X)\n#endif\n"

    assert _blocks(source, assumptions={"Y": True}) == []


@pytest.mark.parametrize(
    "source",
    [
        "#if A\nx\n#elif B\n#elif 1/0\n#endif\n",
        "#if A\nx\n#elif B\n#elif __COUNTER__\n#endif\n",
        "#if A\nx\n#elif B\n#elif UNKNOWN(1)\n#endif\n",
    ],
)
def test_a_run_in_the_middle_is_not_removed_when_a_kept_branch_would_start_being_evaluated(
    source,
):
    assert _branches(source) == []


def test_a_dead_branch_inside_an_empty_block_is_reported_as_dead_not_as_empty():
    source = "#if A\n#if !A\n#endif\n#endif\n"
    kinds = {finding.kind for finding in cpre.analyze_source(source).findings}

    assert cpre.FindingKind.DEAD_BRANCH in kinds
    assert BLOCK not in kinds


def test_a_kept_condition_that_expands_to_a_division_blocks_the_deletion():
    source = "#define BAD (1/0)\n#if A\nx\n#elif B\n#elif BAD\n#endif\n"

    assert _branches(source) == []


def test_a_shorter_run_is_offered_when_the_longest_is_not_safe():
    source = "#if A\nx\n#elif __COUNTER__\n#else\n#endif\n"

    (finding,) = _branches(source)

    assert finding.location.line == 4
    assert rewritten(source, BRANCH) == "#if A\nx\n#elif __COUNTER__\n#endif\n"


def test_a_kept_or_deleted_condition_that_expands_to_nothing_blocks_the_deletion():
    source = "#define EMPTY\n#if A\nx\n#elif EMPTY\n#endif\n"

    assert _branches(source) == []


def test_defined_of_an_empty_macro_is_still_deletable():
    source = "#define EMPTY\n#if A\nx\n#elif defined(EMPTY)\n#endif\n"

    assert len(_branches(source)) == 1


@pytest.mark.parametrize("body", ["1.5", '"s"', "x = 1", "1 +", "UNKNOWN(1)"])
def test_a_macro_that_is_not_a_valid_if_operand_blocks_the_deletion(body):
    source = f"#define V {body}\n#if A\nx\n#elif V\n#endif\n"
    assert _branches(source) == []
    source = f"#define V {body}\n#if V\n#endif\nint y;\n"
    assert _blocks(source) == []


def test_a_valid_macro_operand_is_still_deletable():
    source = "#define V (1 + 2)\n#if A\nx\n#elif V\n#endif\n"

    assert len(_branches(source)) == 1
