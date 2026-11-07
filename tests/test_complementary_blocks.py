"""Adjacent blocks that test a condition and its opposite become #if/#else (issue #241)."""

from __future__ import annotations

import json

import pytest
from block_rewrite_support import apply_edit, findings_of, outputs, rewritten

import cpre
from cpre.cli import main
from cpre.reporting import FindingCounts
from cpre.sarif import sarif_log
from cpre.text_reports import summary_line

KIND = cpre.FindingKind.COMPLEMENTARY_BLOCKS
MERGE = cpre.FindingKind.MERGEABLE_BLOCKS


def _pairs(source: str, **kwargs) -> list[cpre.Finding]:
    return findings_of(source, KIND, **kwargs)


def _lines(finding: cpre.Finding) -> tuple[int, tuple[int, ...]]:
    return finding.location.line, tuple(item.line for item in finding.related_locations)


# --- What is reported ------------------------------------------------------------------------


def test_a_condition_and_its_negation_are_one_finding_with_an_exact_edit():
    source = "#if FLAG_A\nint x;\n#endif\n\n#if !FLAG_A\nint y;\n#endif\n"

    (finding,) = _pairs(source)

    assert _lines(finding) == (1, (5,))
    assert finding.directive == "if"
    assert finding.original_condition == "FLAG_A"
    assert finding.depends_on_assumptions is False
    assert finding.exact_simplification is None
    assert "opposite condition" in finding.reason
    assert "line 5" in finding.reason
    assert finding.edit is not None
    assert finding.edit.confidence is cpre.FixConfidence.EXACT
    assert finding.edit.replacement == "#else\n"
    assert apply_edit(source, finding.edit) == "#if FLAG_A\nint x;\n#else\nint y;\n#endif\n"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#ifdef A\nx;\n#endif\n#ifndef A\ny;\n#endif\n", "#ifdef A\nx;\n#else\ny;\n#endif\n"),
        ("#ifndef A\nx;\n#endif\n#ifdef A\ny;\n#endif\n", "#ifndef A\nx;\n#else\ny;\n#endif\n"),
        (
            "#if defined(A)\nx;\n#endif\n#if !defined(A)\ny;\n#endif\n",
            "#if defined(A)\nx;\n#else\ny;\n#endif\n",
        ),
        (
            "#ifdef A\nx;\n#endif\n#if !defined(A)\ny;\n#endif\n",
            "#ifdef A\nx;\n#else\ny;\n#endif\n",
        ),
        (
            "#if A && B\nx;\n#endif\n#if !A || !B\ny;\n#endif\n",
            "#if A && B\nx;\n#else\ny;\n#endif\n",
        ),
        ("#if A\nx;\n#endif\n#if !!!A\ny;\n#endif\n", "#if A\nx;\n#else\ny;\n#endif\n"),
        ("#if !A\nx;\n#endif\n#if A\ny;\n#endif\n", "#if !A\nx;\n#else\ny;\n#endif\n"),
    ],
)
def test_the_complement_forms_are_found_and_rewritten(source, expected):
    assert rewritten(source, KIND) == expected


def test_a_pair_inside_a_parent_branch_is_found_and_keeps_its_indentation():
    source = "#if P\n  #if A\n  x;\n  #endif\n  #if !A\n  y;\n  #endif\n#endif\n"

    assert rewritten(source, KIND) == "#if P\n  #if A\n  x;\n  #else\n  y;\n  #endif\n#endif\n"


def test_crlf_line_endings_are_kept():
    source = "#if A\r\nx;\r\n#endif\r\n\r\n#if !A\r\ny;\r\n#endif\r\n"

    assert rewritten(source, KIND) == "#if A\r\nx;\r\n#else\r\ny;\r\n#endif\r\n"


def test_empty_bodies_are_reported():
    assert rewritten("#if A\n#endif\n#if !A\n#endif\n", KIND) == "#if A\n#else\n#endif\n"


def test_only_the_first_pair_of_three_blocks_is_reported():
    source = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n#if A\nz;\n#endif\n"

    (finding,) = _pairs(source)

    assert _lines(finding) == (1, (4,))


def test_the_same_condition_is_a_merge_not_a_complement():
    source = "#if A\nx;\n#endif\n#if A\ny;\n#endif\n"

    assert _pairs(source) == []
    assert len(findings_of(source, MERGE)) == 1


# --- What is not reported --------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # A value test and a definedness test are different tests: with ``#define A 0`` both
        # ``#if A`` and ``#ifndef A`` are false.
        "#if A\nx;\n#endif\n#ifndef A\ny;\n#endif\n",
        "#ifndef A\nx;\n#endif\n#if A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n#if !defined(A)\ny;\n#endif\n",
        # Not opposites.
        "#if A\nx;\n#endif\n#if !B\ny;\n#endif\n",
        "#if A && B\nx;\n#endif\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n#if B\ny;\n#endif\n",
        # Something other than blank lines between the blocks.
        "#if A\nx;\n#endif\n// c\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n/* c */\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\nint g;\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n#define Z 1\n#if !A\ny;\n#endif\n",
        # A comment on a directive the rewrite would delete.
        "#if A\nx;\n#endif // end A\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n#if !A // not A\ny;\n#endif\n",
        # Blocks that already have more than one branch.
        "#if A\nx;\n#else\nw;\n#endif\n#if !A\ny;\n#endif\n",
        "#if A\nx;\n#endif\n#if !A\ny;\n#else\nw;\n#endif\n",
        "#if A\nx;\n#elif B\nw;\n#endif\n#if !A\ny;\n#endif\n",
        # Opaque predicates are not compared.
        "#if A > 1\nx;\n#endif\n#if !(A > 1)\ny;\n#endif\n",
        "#if FOO(1)\nx;\n#endif\n#if !FOO(1)\ny;\n#endif\n",
        # Volatile.
        "#if __LINE__\nx;\n#endif\n#if !__LINE__\ny;\n#endif\n",
    ],
)
def test_pairs_that_are_not_safe_or_not_complementary_are_not_reported(source):
    assert _pairs(source) == []


@pytest.mark.parametrize(
    "body",
    [
        "#undef A",
        "#define A 0",
        "#define A 1",
        "#include <h.h>",
        '#pragma push_macro("A")',
        '_Pragma("pop_macro(\\"A\\")")',
        "%:undef A",
    ],
)
def test_a_first_body_that_can_change_the_condition_blocks_the_rewrite(body):
    assert _pairs(f"#if A\n{body}\n#endif\n#if !A\ny;\n#endif\n") == []


def test_a_define_of_an_unrelated_macro_is_fine():
    assert len(_pairs("#if A\n#define B 1\n#endif\n#if !A\ny;\n#endif\n")) == 1


def test_a_macro_the_condition_expands_to_in_the_file_is_followed():
    source = "#define FEATURE LEVEL\n#if FEATURE\n#undef LEVEL\n#endif\n#if !FEATURE\ny;\n#endif\n"

    assert _pairs(source) == []


def test_an_unparenthesized_definition_blocks_the_rewrite_because_negation_would_regroup_it():
    # ``!A`` after expansion is ``!X || Y``, which is not the complement of ``X || Y``.
    source = "#define A X || Y\n#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"

    assert _pairs(source) == []
    original = "#define A X || Y\n#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"
    rewritten_form = "#define A X || Y\n#if A\nx;\n#else\ny;\n#endif\n"
    assert outputs(original, ("X", "Y")) != outputs(rewritten_form, ("X", "Y"))


def test_a_parenthesized_definition_is_fine():
    source = "#define A (X || Y)\n#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"

    assert len(_pairs(source)) == 1


def test_a_dead_or_redundant_block_is_reported_as_such_not_as_a_pair():
    result = cpre.analyze_source("#if A\n#if !A\nx;\n#endif\n#endif\n#if B\n#endif\n")

    assert all(finding.kind is not KIND for finding in result.findings)
    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


def test_assumptions_can_only_remove_the_finding():
    source = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"

    assert len(_pairs(source)) == 1
    assert [f.depends_on_assumptions for f in _pairs(source, assumptions={"B": True})] == [False]
    # The block each assumption makes dead or redundant is reported as such instead.
    expected = {
        True: [(cpre.FindingKind.REDUNDANT_BRANCH, 1), (cpre.FindingKind.DEAD_BRANCH, 4)],
        False: [(cpre.FindingKind.DEAD_BRANCH, 1), (cpre.FindingKind.REDUNDANT_BRANCH, 4)],
    }
    for value, findings in expected.items():
        result = cpre.analyze_source(source, assumptions={"A": value})
        assert [(item.kind, item.location.line) for item in result.findings] == findings


def test_a_pair_that_exhausts_the_work_budget_curtails_the_whole_result():
    pair = "#if A && B\n#endif\n#if !B || !A\n#endif\n"
    unproven = "#if A && B\n#endif\n// c\n#if !B || !A\n#endif\n"
    needed = cpre.analyze_source(unproven, telemetry=True).telemetry.work

    starved = cpre.analyze_source(pair, options=cpre.AnalysisOptions(max_work=needed))

    assert not starved.complete
    assert starved.findings == ()
    assert len(_pairs(pair)) == 1


# --- The rewrite keeps the meaning -----------------------------------------------------------

EQUIVALENT = [
    ("#if A\nx;\n#endif\n#if !A\ny;\n#endif\n", ("A",)),
    ("#ifdef A\nx;\n#endif\n#ifndef A\ny;\n#endif\n", ("A",)),
    ("#if A && B\nx;\n#endif\n#if !A || !B\ny;\n#endif\n", ("A", "B")),
    ("#if A\n#define B 1\n#endif\n#if !A\nz = B;\n#endif\n", ("A", "B")),
    ("#if defined(A)\n#define B 1\n#endif\n#if !defined(A)\nz = B;\n#endif\n", ("A", "B")),
    ("#if P\n#if A\nx;\n#endif\n#if !A\ny;\n#endif\n#endif\n", ("P", "A")),
]


@pytest.mark.parametrize(("source", "names"), EQUIVALENT)
def test_applying_the_edit_does_not_change_the_preprocessed_output(source, names):
    assert outputs(source, names) == outputs(rewritten(source, KIND), names)


def test_reanalysing_the_rewritten_source_reports_no_pair_and_keeps_other_findings():
    source = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n#if B\n#if B\nz;\n#endif\n#endif\n"

    after = cpre.analyze_source(rewritten(source, KIND))

    assert all(finding.kind is not KIND for finding in after.findings)
    assert [finding.kind for finding in after.findings] == [cpre.FindingKind.REDUNDANT_BRANCH]


# --- Tree, counts and rendering --------------------------------------------------------------


def test_the_tree_records_the_pair_on_its_first_block_only():
    result = cpre.analyze_source("#if A\n#endif\n#if !A\n#endif\n")

    first, second = result.tree.groups
    assert [(item.kind, item.lines) for item in first.suggestions] == [
        ("complementary_blocks", (3,))
    ]
    assert first.merge_with == ()
    assert second.suggestions == ()


def test_a_source_without_pairs_has_no_suggestions():
    result = cpre.analyze_source("#if A\n#endif\n#if B\n#endif\n")

    assert all(group.suggestions == () for group in result.tree.groups)


def test_summary_line_and_counts():
    assert summary_line(FindingCounts(complementary=1)) == "Summary  1 complementary block pair"
    assert summary_line(FindingCounts(complementary=2)) == "Summary  2 complementary block pairs"
    assert FindingCounts(complementary=1).failing == 1
    assert (FindingCounts(complementary=1) + FindingCounts(mergeable=1)).total == 2


# --- Command line ----------------------------------------------------------------------------

SOURCE = "#if FLAG_A\nint x;\n#endif\n\n#if !FLAG_A\nint y;\n#endif\n"


def test_text_report_shows_an_else_badge_reason_and_summary(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--color", "never", str(source)]) == 0
    out = capsys.readouterr().out

    assert "ELSE" in out
    assert "L1" in out
    assert (
        "the opposite condition is tested at line 5; the blocks can be one #if with an #else" in out
    )
    assert "Summary  1 complementary block pair" in out.splitlines()


def test_the_else_badge_is_colored_like_the_merge_badge(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    main(["--color", "always", str(source)])

    assert "\033[1;33mELSE\033[0m" in capsys.readouterr().out


def test_fail_on_findings_fails_for_a_pair(tmp_path):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--fail-on-findings", str(source)]) == 1


def test_json_lists_the_suggestion_on_the_group(tmp_path, capsys):
    source = tmp_path / "a.c"
    source.write_text(SOURCE)

    assert main(["--json", "--verbose", str(source)]) == 0
    groups = json.loads(capsys.readouterr().out)["groups"]

    assert groups[0]["suggestions"] == [{"kind": "complementary_blocks", "lines": [5]}]
    assert groups[0]["merge_with"] == []
    assert groups[1]["suggestions"] == []


def test_sarif_has_a_rule_related_locations_and_a_fix():
    analysis = cpre.analyze_source(SOURCE, filename="a.c")
    (sarif_result,) = sarif_log([analysis], tool_version="1")["runs"][0]["results"]

    assert sarif_result["ruleId"] == "CPRE006"
    assert sarif_result["level"] == "warning"
    assert sarif_result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    (related,) = sarif_result["relatedLocations"]
    assert related["physicalLocation"]["region"] == {"startLine": 5}
    assert related["message"]["text"] == "Block that can become the #else of the first one"
    (fix,) = sarif_result["fixes"]
    assert fix["description"]["text"] == "Make the block starting at line 5 the #else of this one"
    (replacement,) = fix["artifactChanges"][0]["replacements"]
    assert replacement["insertedContent"]["text"] == "#else\n"
    assert replacement["deletedRegion"] == {
        "startLine": 3,
        "endLine": 6,
        "startColumn": 1,
        "endColumn": 1,
    }


def test_a_suggestion_of_a_kind_this_version_does_not_know_is_ignored_by_the_text_report():
    from cpre.model import BlockSuggestion
    from cpre.reporting import count_findings, has_findings, render_report

    result = cpre.analyze_source("#if A\n#endif\n")
    result.tree.groups[0].suggestions = (BlockSuggestion("from_the_future", (3,)),)

    text, _ = render_report(result.tree, verbose=True)

    assert "from_the_future" not in text
    assert count_findings(result.tree) == FindingCounts()
    assert not has_findings(result.tree)


def test_a_line_test_after_the_pair_blocks_the_rewrite_but_one_before_does_not():
    pair = "#if A\nx;\n#endif\n#if !A\ny;\n#endif\n"

    assert _pairs(pair + "#if __LINE__ > 100\n#endif\n") == []
    assert len(_pairs("#if __LINE__ > 100\n#endif\n" + pair)) == 1


# --- Line endings ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # The ``#else`` takes the line break of the ``#endif`` it replaces, not the opener's.
        (
            "#if A\r\nx;\r\n#endif\n\n#if !A\r\ny;\r\n#endif\r\n",
            "#if A\r\nx;\r\n#else\ny;\r\n#endif\r\n",
        ),
        (
            "#if A\nx;\n#endif\r\n\r\n#if !A\ny;\n#endif\n",
            "#if A\nx;\n#else\r\ny;\n#endif\n",
        ),
        # A lone CR ends a line too.
        ("#if A\rx;\r#endif\r\r#if !A\ry;\r#endif\r", "#if A\rx;\r#else\ry;\r#endif\r"),
    ],
)
def test_the_else_takes_the_line_break_of_the_endif_it_replaces(source, expected):
    assert rewritten(source, KIND) == expected


# --- Assumptions -----------------------------------------------------------------------------


def test_a_finding_that_rests_on_telling_defined_from_value_apart_needs_no_assumptions():
    # Without assumptions ``defined(A)`` and ``A`` are one test, so the nested ``#if A`` is
    # redundant and there is no pair. An unrelated assumption must not make one appear.
    source = "#ifdef A\n#if A\nx;\n#endif\n#if !A\ny;\n#endif\n#endif\n"

    assert _pairs(source) == []
    assert _pairs(source, assumptions={"B": True}) == []
    assert _pairs(source, assumptions={"B": False}) == []
