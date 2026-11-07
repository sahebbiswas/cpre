"""A block whose only content is one block collapses into one #if A && B (issue #242)."""

from __future__ import annotations

import json

import pytest
from block_rewrite_support import apply_edit, findings_of, outputs, rewritten

import cpre
from cpre.cli import main
from cpre.reporting import FindingCounts
from cpre.sarif import sarif_log
from cpre.text_reports import summary_line

KIND = cpre.FindingKind.COLLAPSIBLE_NESTING


def _nests(source: str, **kwargs) -> list[cpre.Finding]:
    return findings_of(source, KIND, **kwargs)


def _lines(finding: cpre.Finding) -> tuple[int, tuple[int, ...]]:
    return finding.location.line, tuple(item.line for item in finding.related_locations)


# --- What is reported ------------------------------------------------------------------------


def test_a_block_holding_only_a_block_is_one_finding_with_an_exact_edit():
    source = "#if FLAG_A\n#if FLAG_B\nint x;\n#endif\n#endif\n"

    (finding,) = _nests(source)

    assert _lines(finding) == (1, (2,))
    assert finding.directive == "if"
    assert finding.original_condition == "FLAG_A"
    assert finding.depends_on_assumptions is False
    assert "nested block at line 2" in finding.reason
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert apply_edit(source, finding.edit) == "#if FLAG_A && FLAG_B\nint x;\n#endif\n"


def test_a_chain_of_three_is_one_finding_listing_both_inner_blocks():
    source = "#if A\n#if B\n#if C\nx;\n#endif\n#endif\n#endif\n"

    (finding,) = _nests(source)

    assert _lines(finding) == (1, (2, 3))
    assert "nested blocks at lines 2 and 3" in finding.reason
    assert rewritten(source, KIND) == "#if A && B && C\nx;\n#endif\n"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#ifdef A\n#ifdef B\nx;\n#endif\n#endif\n", "#if defined(A) && defined(B)\nx;\n#endif\n"),
        (
            "#ifndef A\n#ifndef B\nx;\n#endif\n#endif\n",
            "#if !defined(A) && !defined(B)\nx;\n#endif\n",
        ),
        ("#ifdef A\n#if !B\nx;\n#endif\n#endif\n", "#if defined(A) && !B\nx;\n#endif\n"),
        ("#if A\n#if defined(B)\nx;\n#endif\n#endif\n", "#if A && defined(B)\nx;\n#endif\n"),
        # Anything but a name or a defined test is parenthesized: ``||`` binds looser than ``&&``.
        ("#if A || B\n#if C\nx;\n#endif\n#endif\n", "#if (A || B) && C\nx;\n#endif\n"),
        ("#if A\n#if B || C\nx;\n#endif\n#endif\n", "#if A && (B || C)\nx;\n#endif\n"),
        ("#if A\n#if (B || C)\nx;\n#endif\n#endif\n", "#if A && (B || C)\nx;\n#endif\n"),
        ("#if A > 1\n#if B\nx;\n#endif\n#endif\n", "#if (A > 1) && B\nx;\n#endif\n"),
        ("#if A\n#if B ? C : D\nx;\n#endif\n#endif\n", "#if A && (B ? C : D)\nx;\n#endif\n"),
    ],
)
def test_the_collapsed_condition_for_each_directive_form(source, expected):
    assert rewritten(source, KIND) == expected


def test_blank_lines_around_the_inner_block_are_allowed_and_dropped():
    source = "#if A\n\n\n#if B\nx;\n#endif\n\n#endif\n"

    assert rewritten(source, KIND) == "#if A && B\nx;\n#endif\n"


def test_the_inner_body_keeps_its_indentation_and_the_outer_endif_its_comment():
    source = "#if A\n  #if B\n  x;\n  y;\n  #endif\n#endif // end A\n"

    assert rewritten(source, KIND) == "#if A && B\n  x;\n  y;\n#endif // end A\n"


def test_comments_in_the_inner_body_and_on_the_inner_endif_do_not_matter():
    source = "#if A\n#if B\n// keep\nx; /* me */\n#endif\n#endif\n"

    assert rewritten(source, KIND) == "#if A && B\n// keep\nx; /* me */\n#endif\n"


def test_indentation_and_line_endings_of_the_outer_block_are_kept():
    assert rewritten("  #if A\n  #if B\n  x;\n  #endif\n  #endif\n", KIND) == (
        "  #if A && B\n  x;\n  #endif\n"
    )
    assert rewritten("#if A\r\n#if B\r\nx;\r\n#endif\r\n#endif\r\n", KIND) == (
        "#if A && B\r\nx;\r\n#endif\r\n"
    )


def test_a_file_without_a_final_newline_is_handled():
    assert rewritten("#if A\n#if B\nx;\n#endif\n#endif", KIND) == "#if A && B\nx;\n#endif"


def test_a_chain_inside_another_block_is_found():
    source = "#if P\nint s;\n#if A\n#if B\nx;\n#endif\n#endif\n#endif\n"

    (finding,) = _nests(source)

    assert _lines(finding) == (3, (4,))
    assert rewritten(source, KIND) == "#if P\nint s;\n#if A && B\nx;\n#endif\n#endif\n"


def test_a_macro_defined_name_with_a_complex_replacement_is_parenthesized():
    source = "#define M X || Y\n#if M\n#if B\nx;\n#endif\n#endif\n"

    assert rewritten(source, KIND) == "#define M X || Y\n#if (M) && B\nx;\n#endif\n"


# --- What is not reported --------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # Something besides the one inner block.
        "#if A\n#if B\nx;\n#endif\ny;\n#endif\n",
        "#if A\ny;\n#if B\nx;\n#endif\n#endif\n",
        "#if A\n#if B\nx;\n#endif\n#if C\ny;\n#endif\n#endif\n",
        "#if A\n// c\n#if B\nx;\n#endif\n#endif\n",
        "#if A\n#if B\nx;\n#endif\n/* c */\n#endif\n",
        "#if A\n#define Z 1\n#if B\nx;\n#endif\n#endif\n",
        "#if A\n#if B\nx;\n#endif\n#undef Z\n#endif\n",
        # Branches.
        "#if A\n#if B\nx;\n#endif\n#else\nz;\n#endif\n",
        "#if A\n#if B\nx;\n#else\nw;\n#endif\n#endif\n",
        "#if A\n#if B\nx;\n#elif C\nw;\n#endif\n#endif\n",
        # A comment on a line the edit rewrites or deletes.
        "#if A // a\n#if B\nx;\n#endif\n#endif\n",
        "#if A\n#if B // b\nx;\n#endif\n#endif\n",
        "#if A\n#if B\nx;\n#endif // b\n#endif\n",
        # Line continuations.
        "#if A && \\\n B\n#if C\nx;\n#endif\n#endif\n",
        # Volatile conditions.
        "#if A\n#if __LINE__ > 1\nx;\n#endif\n#endif\n",
        "#if __COUNTER__\n#if B\nx;\n#endif\n#endif\n",
        # A name built by token pasting, or a _Pragma the inner test would run when the outer
        # test is false.
        "#define P(a, b) a##b\n#if A\n#if P(__LI, NE__)\nx;\n#endif\n#endif\n",
        '#define PR _Pragma("once")\n#if A\n#if PR\nx;\n#endif\n#endif\n',
        # A condition after the chain tests its own line number, which the rewrite moves.
        "#if A\n#if B\nx;\n#endif\n#endif\n#if __LINE__ > 100\n#endif\n",
        # An empty outer block, or two siblings.
        "#if A\n#endif\n",
        "#if A\n#endif\n#if B\n#endif\n",
    ],
)
def test_blocks_that_are_not_just_one_nested_block_are_not_reported(source):
    assert _nests(source) == []


def test_a_nested_block_with_the_same_condition_is_redundant_not_collapsible():
    result = cpre.analyze_source("#if A\n#if A\nx;\n#endif\n#endif\n")

    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.REDUNDANT_BRANCH]


def test_a_block_with_a_simpler_equivalent_condition_is_left_to_the_simplification_first():
    # The inner ``ROOT && CHILD`` simplifies to ``CHILD`` in context; collapsing now would copy it.
    source = "#if ROOT\n#if ROOT && CHILD\nx;\n#endif\n#endif\n"

    result = cpre.analyze_source(source)

    assert [finding.kind for finding in result.findings] == [
        cpre.FindingKind.CONTEXTUAL_SIMPLIFICATION
    ]
    assert (
        rewritten("#if ROOT\n#if CHILD\nx;\n#endif\n#endif\n", KIND)
        == "#if ROOT && CHILD\nx;\n#endif\n"
    )


def test_a_nested_block_that_can_never_run_is_dead_not_collapsible():
    result = cpre.analyze_source("#if A\n#if !A\nx;\n#endif\n#endif\n")

    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


def test_assumptions_can_only_remove_the_finding():
    source = "#if A\n#if B\nx;\n#endif\n#endif\n"

    assert [f.depends_on_assumptions for f in _nests(source, assumptions={"C": True})] == [False]
    # Each assumption makes a block dead or redundant, which is reported instead.
    expected = {
        ("A", True): [(cpre.FindingKind.REDUNDANT_BRANCH, 1)],
        ("A", False): [
            (cpre.FindingKind.DEAD_BRANCH, 1),
            (cpre.FindingKind.DEAD_BRANCH, 2),
        ],
        ("B", False): [(cpre.FindingKind.DEAD_BRANCH, 2)],
    }
    for (name, value), findings in expected.items():
        result = cpre.analyze_source(source, assumptions={name: value})
        assert [(item.kind, item.location.line) for item in result.findings] == findings


def test_only_the_outermost_block_of_a_chain_is_reported_and_not_each_link():
    source = "#if A\n#if B\n#if C\nx;\n#endif\n#endif\n#endif\n"

    assert len(_nests(source)) == 1


def test_a_chain_stops_where_the_next_block_has_a_sibling():
    source = "#if A\n#if B\n#if C\nx;\n#endif\ny;\n#endif\n#endif\n"

    (finding,) = _nests(source)

    assert _lines(finding) == (1, (2,))
    assert rewritten(source, KIND) == "#if A && B\n#if C\nx;\n#endif\ny;\n#endif\n"


# --- The rewrite keeps the meaning -----------------------------------------------------------

EQUIVALENT = [
    ("#if A\n#if B\nx;\n#endif\n#endif\n", ("A", "B")),
    ("#ifdef A\n#ifndef B\nx;\n#endif\n#endif\n", ("A", "B")),
    ("#if A\n#if B\n#if C\nx;\n#else\n#endif\n#endif\n#endif\n", ("A", "B", "C")),
    ("#if A || B\n#if C\nx;\n#endif\n#endif\n", ("A", "B", "C")),
    ("#if A\n#if B || C\nx;\n#endif\n#endif\n", ("A", "B", "C")),
    ("#if A\n#if B\n#define C 1\n#endif\n#endif\nz = C;\n", ("A", "B", "C")),
    ("#if A\n#if B > 0\nx;\n#endif\n#endif\n", ("A", "B")),
    ("#define M B || C\n#if A\n#if M\nx;\n#endif\n#endif\n", ("A", "B", "C")),
    ("#define M B || C\n#if M\n#if A\nx;\n#endif\n#endif\n", ("A", "B", "C")),
]


@pytest.mark.parametrize(("source", "names"), EQUIVALENT)
def test_applying_the_edit_does_not_change_the_preprocessed_output(source, names):
    assert outputs(source, names) == outputs(rewritten(source, KIND), names)


def test_reanalysing_the_rewritten_source_reports_no_nesting():
    source = "#if A\n#if B\n#if C\nx;\n#endif\n#endif\n#endif\n"

    after = cpre.analyze_source(rewritten(source, KIND))

    assert all(finding.kind is not KIND for finding in after.findings)


# --- Tree, counts and rendering --------------------------------------------------------------


def test_the_tree_records_the_chain_on_the_outermost_group_only():
    result = cpre.analyze_source("#if A\n#if B\n#if C\nx;\n#endif\n#endif\n#endif\n")

    (outer,) = result.tree.groups
    assert [(item.kind, item.lines) for item in outer.suggestions] == [
        ("collapsible_nesting", (2, 3))
    ]
    inner = outer.branches[0].children[0]
    assert inner.suggestions == ()


def test_the_style_suggestion_counts_but_does_not_fail():
    counts = FindingCounts(collapsible=2)

    assert counts.failing == 0
    assert counts.total == 2
    assert summary_line(counts) == "Summary  2 collapsible nestings"
    assert summary_line(FindingCounts(collapsible=1)) == "Summary  1 collapsible nesting"


# --- Command line ----------------------------------------------------------------------------

SOURCE = "#if FLAG_A\n#if FLAG_B\nint x;\n#endif\n#endif\n"


def test_text_report_shows_a_nest_badge_reason_and_summary(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    assert "NEST" in out
    assert "the only content of this block is a nested block at line 2" in out
    assert "Summary  1 collapsible nesting" in out.splitlines()


def test_fail_on_findings_does_not_fail_for_a_style_suggestion(tmp_path):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--fail-on-findings", str(source)]) == 0


def test_fail_on_findings_still_fails_when_another_finding_is_present(tmp_path):
    source = tmp_path / "a.c"
    source.write_text(SOURCE + "#if C\n#if C\n#endif\n#endif\n")

    assert main(["--fail-on-findings", str(source)]) == 1


def test_json_lists_the_suggestion_on_the_group(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--json", "--verbose", str(source)]) == 0
    (group,) = json.loads(capsys.readouterr().out)["groups"]

    assert group["suggestions"] == [{"kind": "collapsible_nesting", "lines": [2]}]


def test_sarif_has_a_note_level_rule_related_locations_and_a_fix():
    analysis = cpre.analyze_source(SOURCE, filename="a.c")
    (result,) = sarif_log([analysis], tool_version="1")["runs"][0]["results"]

    assert result["ruleId"] == "CPRE008"
    assert result["level"] == "note"
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    (related,) = result["relatedLocations"]
    assert related["physicalLocation"]["region"] == {"startLine": 2}
    (fix,) = result["fixes"]
    assert fix["description"]["text"] == (
        "Collapse the nested blocks starting at line 2 into this one with &&"
    )
    (replacement,) = fix["artifactChanges"][0]["replacements"]
    assert replacement["insertedContent"]["text"] == "#if FLAG_A && FLAG_B\nint x;\n#endif\n"
    assert replacement["deletedRegion"] == {
        "startLine": 1,
        "endLine": 6,
        "startColumn": 1,
        "endColumn": 1,
    }


@pytest.mark.parametrize(
    "outer",
    ["(A == '(') || (B == ')')", "(X == ')') || (Y == '(')", "(A == '\\'') || (B == '(')"],
)
def test_parentheses_in_character_literals_do_not_count_as_grouping(outer):
    source = f"#if {outer}\n#if C\nint x;\n#endif\n#endif\n"

    (finding,) = _nests(source)

    assert finding.edit is not None
    assert f"#if ({outer}) && C" in finding.edit.replacement


@pytest.mark.parametrize("inner", ["FOO(1)", "B C"])
def test_an_inner_condition_that_is_not_read_when_the_outer_is_false_is_not_collapsed(inner):
    source = f"#if A\n#if {inner}\nx;\n#endif\n#endif\n"

    assert _nests(source) == []


def test_a_nesting_edit_may_contain_the_edit_of_another_finding():
    source = "#if A\n#if B\n#if C\nx;\n#endif\n#if C\ny;\n#endif\n#endif\n#endif\n"
    found = cpre.analyze_source(source).findings
    nesting = next(item for item in found if item.kind is KIND)
    merge = next(item for item in found if item.kind is cpre.FindingKind.MERGEABLE_BLOCKS)

    assert nesting.edit is not None and merge.edit is not None
    assert nesting.edit.range.start.line < merge.edit.range.start.line
    assert merge.edit.range.end.line < nesting.edit.range.end.line
