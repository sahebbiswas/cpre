"""Partial analysis results: per-component limits never hide or fake other results.

When one independent component exceeds ``max_atoms`` or ``max_bdd_nodes``, the
other components are still analyzed and reported, and the failing component is
described by a structured :class:`cpre.IncompleteComponent`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cpre
from cpre.analysis import analyze_tree, analyze_tree_partial
from cpre.cli import main
from cpre.expressions import conjunction, disjunction, negate
from cpre.model import Variable
from cpre.parser import parse_source
from cpre.robdd import AnalysisLimitExceeded, ResourceLimits
from cpre.sarif import sarif_log

# Lines 1-5: a redundant nested ``#if A`` (one finding at line 2).
SMALL = "#if A\n#if A\nx;\n#endif\n#endif\n"
# Lines 6-8 when appended to SMALL: three atoms and a dead ``#elif``.
LARGE = "#if B && C && D\n#elif B && C && D\n#endif\n"
SOURCE = SMALL + LARGE
WIDE = "#if " + " || ".join(f"W_{index}" for index in range(70)) + "\n#elif W_0\n#endif\n"


def _key(finding: cpre.Finding) -> tuple[object, ...]:
    return (finding.kind, finding.location.line, finding.reason, finding.depends_on_assumptions)


def _branches(groups):
    for group in groups:
        for branch in group.branches:
            yield branch
            yield from _branches(branch.children)


def test_one_complete_and_one_over_budget_component():
    result = cpre.analyze_source(
        SOURCE, filename="mixed.c", options=cpre.AnalysisOptions(max_atoms=2)
    )

    assert not result.complete
    assert result.partial
    assert [(f.kind, f.location.line) for f in result.findings] == [
        (cpre.FindingKind.REDUNDANT_BRANCH, 2)
    ]
    (component,) = result.incomplete_components
    assert component.index == 0
    assert component.groups == (
        cpre.IncompleteGroup(
            location=cpre.SourceLocation(6),
            end_line=8,
            directive="if",
            condition="B && C && D",
        ),
    )
    assert component.atoms == ("B", "C", "D")
    assert component.resource == "atoms"
    (diagnostic,) = component.diagnostics
    assert diagnostic == cpre.AnalysisIncomplete(
        code=cpre.ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
        resource="atoms",
        limit=2,
        observed=3,
        message="analysis limit exceeded for atoms: 3 > 2",
        location=None,
        component=0,
    )
    assert result.incomplete == (diagnostic,)
    assert component.contains_line(7) and not component.contains_line(5)


def test_over_budget_component_branches_carry_no_analysis():
    result = cpre.analyze_source(SOURCE, options=cpre.AnalysisOptions(max_atoms=2))

    complete_group, incomplete_group = result.tree.groups
    assert all(branch.analysis is not None for branch in _branches([complete_group]))
    assert all(branch.analysis is None for branch in _branches([incomplete_group]))


def test_bdd_node_limit_in_one_component_keeps_the_others():
    # The single-atom component needs one node; the three-atom component needs more.
    result = cpre.analyze_source(SOURCE, options=cpre.AnalysisOptions(max_bdd_nodes=2))

    assert result.partial
    assert [f.location.line for f in result.findings] == [2]
    (component,) = result.incomplete_components
    assert component.resource == "bdd_nodes"
    assert component.diagnostics[0].location == cpre.SourceLocation(6)
    assert [group.location.line for group in component.groups] == [6]


def test_complete_analysis_is_unchanged():
    result = cpre.analyze_source(SOURCE)

    assert result.complete
    assert not result.partial
    assert result.incomplete == ()
    assert result.incomplete_components == ()
    assert [(f.kind, f.location.line) for f in result.findings] == [
        (cpre.FindingKind.REDUNDANT_BRANCH, 2),
        (cpre.FindingKind.DEAD_BRANCH, 7),
    ]
    assert all(branch.analysis is not None for branch in _branches(result.tree.groups))


def test_partial_findings_equal_full_findings_outside_incomplete_components():
    source = SMALL + WIDE + SMALL + LARGE
    partial = cpre.analyze_source(source)
    full = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=100))

    assert partial.partial and full.complete
    (component,) = partial.incomplete_components
    assert [group.location.line for group in component.groups] == [6]
    expected = [
        _key(finding)
        for finding in full.findings
        if not component.contains_line(finding.location.line)
    ]
    assert [_key(finding) for finding in partial.findings] == expected
    assert len(expected) == len(full.findings) - 1


def test_several_incomplete_components_are_ordered_and_indexed():
    source = "#if A\n#endif\n#if B && C && D\n#endif\n#if E && F && G && H\n#endif\n#if B\n#endif\n"

    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))

    assert result.partial
    assert [c.index for c in result.incomplete_components] == [0, 1]
    assert [[g.location.line for g in c.groups] for c in result.incomplete_components] == [
        [3, 7],
        [5],
    ]
    assert [c.atoms for c in result.incomplete_components] == [
        ("B", "C", "D"),
        ("E", "F", "G", "H"),
    ]
    assert [(d.component, d.observed) for d in result.incomplete] == [(0, 3), (1, 4)]


def test_work_exhaustion_is_still_a_whole_source_incomplete_result():
    result = cpre.analyze_source(SOURCE, options=cpre.AnalysisOptions(max_work=5))

    assert not result.complete
    assert not result.partial
    assert result.findings == ()
    assert result.incomplete_components == ()
    assert result.incomplete[0].resource == "work"
    assert result.incomplete[0].component is None


def test_partial_results_under_assumptions():
    source = "#if Q\n#endif\n" + LARGE

    result = cpre.analyze_source(
        source, assumptions={"Q": False}, options=cpre.AnalysisOptions(max_atoms=2)
    )

    assert result.partial
    assert [(f.kind, f.location.line, f.depends_on_assumptions) for f in result.findings] == [
        (cpre.FindingKind.DEAD_BRANCH, 1, True)
    ]
    (component,) = result.incomplete_components
    assert component.atoms == ("B", "C", "D", "defined(B)", "defined(C)", "defined(D)")


MEDIUM = "#if " + " || ".join(f"M_{index}" for index in range(10)) + "\n#elif M_0\n#endif\n"


@pytest.mark.parametrize("max_atoms", [1, 2, 3, 4, 10, 11, 20])
@pytest.mark.parametrize("max_bdd_nodes", [1, 2, 4, 100_000])
@pytest.mark.parametrize("assumptions", [None, {"A": True}, {"B": False}])
def test_incomplete_analysis_is_never_represented_as_complete(
    max_atoms, max_bdd_nodes, assumptions
):
    source = SMALL + MEDIUM + LARGE + "#if A && E\n#endif\n"
    options = cpre.AnalysisOptions(max_atoms=max_atoms, max_bdd_nodes=max_bdd_nodes)

    result = cpre.analyze_source(source, assumptions=assumptions, options=options)
    full = cpre.analyze_source(
        source, assumptions=assumptions, options=cpre.AnalysisOptions(max_atoms=100)
    )

    unanalyzed = [b for b in _branches(result.tree.groups) if b.analysis is None]
    assert result.complete == (not unanalyzed)
    assert result.complete == (not result.incomplete)
    if result.complete:
        assert [_key(f) for f in result.findings] == [_key(f) for f in full.findings]
        return
    if not result.partial:
        assert result.findings == ()
        return
    assert {d.component for d in result.incomplete} == {
        c.index for c in result.incomplete_components
    }
    for finding in result.findings:
        assert not any(c.contains_line(finding.location.line) for c in result.incomplete_components)
        assert _key(finding) in {_key(f) for f in full.findings}
    for component in result.incomplete_components:
        for group in component.groups:
            assert all(
                b.analysis is None
                for g in result.tree.groups
                if g.line == group.location.line
                for b in _branches([g])
            )


# --- engine ---------------------------------------------------------------------------


def test_analyze_tree_still_raises_on_any_limit():
    with pytest.raises(AnalysisLimitExceeded):
        analyze_tree(parse_source(SOURCE), limits=ResourceLimits(max_atoms=2))


def test_analyze_tree_partial_records_component_failures():
    outcome = analyze_tree_partial(parse_source(SOURCE), limits=ResourceLimits(max_atoms=2))

    assert outcome.group_components[0] != outcome.group_components[1]
    assert set(outcome.failures) == {outcome.group_components[1]}
    assert outcome.failures[outcome.group_components[1]].resource == "atoms"


def test_contradictory_context_in_an_over_budget_component_still_kills_every_group():
    # The context constrains B, C, D (the over-budget component) contradictorily, so
    # every other group is dead even though that component could not be analyzed.
    b, c = Variable("B"), Variable("C")
    contradiction = conjunction(disjunction(b, c), negate(b), negate(c))

    outcome = analyze_tree_partial(
        parse_source(SOURCE, distinguish_defined=True),
        assumptions=contradiction,
        limits=ResourceLimits(max_atoms=2),
    )

    first_group = outcome.tree.groups[0]
    assert [branch.analysis.status for branch in _branches([first_group])] == ["dead", "dead"]
    assert outcome.group_components[1] in outcome.failures


# --- reporting surfaces ---------------------------------------------------------------


def test_cli_text_separates_findings_from_incomplete_analysis(tmp_path, capsys):
    path = tmp_path / "mixed.c"
    path.write_text(SMALL + WIDE, encoding="utf-8")

    exit_code = main([str(path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "2: #if A [redundant]" in captured.out
    assert "Incomplete analysis (no findings are reported for these conditionals):" in captured.out
    assert "6-8: #if W_0 ||" in captured.out
    assert "[incomplete: analysis limit exceeded for atoms: 70 > 64]" in captured.out
    assert (
        f"{path}: line 6: conditional at lines 6-8 not analyzed "
        "(analysis limit exceeded for atoms: 70 > 64); findings cover only the rest of the file"
    ) in captured.err


def test_cli_json_has_stable_incomplete_fields(tmp_path, capsys):
    path = tmp_path / "mixed.c"
    path.write_text(SMALL + WIDE, encoding="utf-8")

    exit_code = main([str(path), "--json", "--no-macros"])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert data["complete"] is False
    assert data["partial"] is True
    assert [group["line"] for group in data["groups"]] == [1]
    diagnostic = {
        "code": "analysis_limit_exceeded",
        "resource": "atoms",
        "limit": 64,
        "observed": 70,
        "message": "analysis limit exceeded for atoms: 70 > 64",
        "line": None,
        "component": 0,
    }
    assert data["incomplete"] == [diagnostic]
    (component,) = data["incomplete_components"]
    assert component["index"] == 0
    assert component["groups"] == [
        {
            "line": 6,
            "end_line": 8,
            "directive": "if",
            "condition": " || ".join(f"W_{index}" for index in range(70)),
        }
    ]
    assert component["atoms"] == [f"W_{index}" for index in range(70)]
    assert component["diagnostics"] == [diagnostic]


def test_cli_json_marks_complete_files(tmp_path, capsys):
    path = tmp_path / "clean.c"
    path.write_text(SMALL, encoding="utf-8")

    assert main([str(path), "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["complete"] is True
    assert data["partial"] is False
    assert data["incomplete"] == []
    assert data["incomplete_components"] == []


def test_cli_batch_json_keeps_incomplete_files_without_verbose(tmp_path, capsys):
    (tmp_path / "a_quiet.c").write_text("#if A\n#endif\n", encoding="utf-8")
    (tmp_path / "b_wide.c").write_text(WIDE, encoding="utf-8")

    exit_code = main([str(tmp_path), "--recursive", "--json"])

    files = json.loads(capsys.readouterr().out)["files"]
    assert exit_code == 2
    assert [Path(f["path"]).name for f in files] == ["b_wide.c"]
    assert files[0]["complete"] is False


def test_sarif_reports_complete_findings_and_incomplete_components():
    analysis = cpre.analyze_source(SMALL + WIDE, filename="src/mixed.c")

    run = sarif_log([analysis], tool_version="0")["runs"][0]

    assert [r["ruleId"] for r in run["results"]] == ["CPRE002"]
    invocation = run["invocations"][0]
    assert invocation["executionSuccessful"] is False
    (notification,) = invocation["toolExecutionNotifications"]
    assert notification["properties"]["scope"] == "component"
    assert notification["properties"]["component"]["groups"] == [{"startLine": 6, "endLine": 8}]
    assert notification["locations"][0]["physicalLocation"]["region"] == {"startLine": 6}
    assert "conditionals starting at line 6 were not analyzed" in notification["message"]["text"]
