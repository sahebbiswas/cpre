"""Boolean analysis is partitioned into independent atom-dependency components.

``max_atoms`` and ``max_bdd_nodes`` apply per connected component; ``max_work``
remains one global cap shared by every component.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import cpre
from cpre.analysis import analyze_source as engine_analyze_source
from cpre.expressions import conjunction, disjunction, negate
from cpre.model import FALSE, Variable
from cpre.robdd import AnalysisBudget, ResourceLimits


def _redundant_pair(name: str) -> str:
    """Five lines; the nested ``#if`` is redundant, so the pair yields one finding."""
    return f"#if {name}\n#if {name}\nx;\n#endif\n#endif\n"


def _summary(result: cpre.AnalysisResult, offset: int = 0) -> list[tuple[object, ...]]:
    return [
        (
            finding.kind,
            finding.location.line - offset,
            finding.directive,
            finding.original_condition,
            finding.reason,
            finding.simplified_condition,
            finding.contextual_condition,
            finding.depends_on_assumptions,
        )
        for finding in result.findings
    ]


def _incomplete(source: str, **limits: int) -> cpre.AnalysisIncomplete | None:
    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(**limits))
    return result.incomplete[0] if result.incomplete else None


def _incomplete_with(
    source: str, assumptions: dict[str, bool], **limits: int
) -> cpre.AnalysisIncomplete | None:
    result = cpre.analyze_source(
        source, assumptions=assumptions, options=cpre.AnalysisOptions(**limits)
    )
    return result.incomplete[0] if result.incomplete else None


# --- independent components -------------------------------------------------------------


def test_many_independent_groups_complete_beyond_the_default_atom_limit():
    source = "".join(_redundant_pair(f"F_{index}") for index in range(100))

    result = cpre.analyze_source(source)

    assert result.complete
    assert [finding.location.line for finding in result.findings] == [
        2 + 5 * index for index in range(100)
    ]
    assert {finding.kind for finding in result.findings} == {cpre.FindingKind.REDUNDANT_BRANCH}


def test_one_atom_per_component_is_enough_for_any_number_of_groups():
    source = "".join(f"#if F_{index}\n#endif\n" for index in range(300))

    assert cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=1)).complete


def test_partitioned_findings_equal_findings_of_each_group_analyzed_alone():
    snippets = [
        "#if A\n#elif A\ny;\n#endif\n",  # dead #elif
        "#if B || !B\nx;\n#endif\n",  # simplifiable condition
        "#if C\n#if !C\nz;\n#endif\n#endif\n",  # nested dead branch
        "#if D && E\n#else\nw;\n#endif\n",  # clean
        "#if F\n#if F || G\nv;\n#endif\n#endif\n",  # contextual simplification
        "#ifdef H\n#elif defined(H)\nu;\n#endif\n",  # dead #elif through defined()
    ]
    combined = "".join(snippets)

    expected: list[tuple[object, ...]] = []
    offset = 0
    for snippet in snippets:
        expected.extend(_summary(cpre.analyze_source(snippet), offset=-offset))
        offset += snippet.count("\n")

    assert expected, "fixture must exercise real findings"
    assert _summary(cpre.analyze_source(combined)) == expected


def test_partitioned_findings_stay_correct_with_unrelated_groups_over_the_old_limit():
    filler = "".join(f"#if F_{index}\nx;\n#endif\n" for index in range(120))
    nested_dead = "#if A\n#if !A\nx;\n#endif\n#endif\n"
    source = filler + nested_dead + filler

    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))

    assert result.complete
    assert [(f.kind, f.location.line) for f in result.findings] == [
        (cpre.FindingKind.DEAD_BRANCH, 3 * 120 + 2)
    ]


def test_two_components_exceeding_the_limit_together_are_both_analyzed():
    source = "#if A && B && C\n#endif\n#if D && E && F\n#endif\n"

    assert cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=3)).complete
    assert cpre.analyze_source(source).complete
    limited = _incomplete(source, max_atoms=2)
    assert limited is not None
    assert (limited.resource, limited.limit, limited.observed) == ("atoms", 2, 3)


# --- connected components stay together ----------------------------------------------


def test_single_component_over_the_limit_remains_incomplete():
    source = "#if " + " || ".join(f"A_{index}" for index in range(65)) + "\n#endif\n"

    result = cpre.analyze_source(source)

    assert not result.complete
    assert result.findings == ()
    diagnostic = result.incomplete[0]
    assert (diagnostic.resource, diagnostic.limit, diagnostic.observed) == ("atoms", 64, 65)
    assert diagnostic.location is None


def test_groups_chained_through_shared_atoms_form_one_component():
    source = "".join(f"#if A_{index} && A_{index + 1}\n#endif\n" for index in range(70))

    diagnostic = _incomplete(source)

    assert diagnostic is not None
    assert (diagnostic.resource, diagnostic.limit, diagnostic.observed) == ("atoms", 64, 71)


def test_shared_atom_couples_two_groups_but_disjoint_atoms_do_not():
    coupled = "#if A && B\n#endif\n#if B && C\n#endif\n"
    disjoint = "#if A && B\n#endif\n#if C && D\n#endif\n"

    coupled_diagnostic = _incomplete(coupled, max_atoms=2)
    assert coupled_diagnostic is not None
    assert coupled_diagnostic.observed == 3
    assert _incomplete(disjoint, max_atoms=2) is None
    assert _incomplete(coupled, max_atoms=3) is None


def test_negated_and_nested_uses_of_an_atom_share_a_component():
    negated = "#if A\n#endif\n#if !A\n#endif\n"
    nested = "#if A\n#if B\n#endif\n#endif\n"

    assert _incomplete(negated, max_atoms=1) is None
    nested_diagnostic = _incomplete(nested, max_atoms=1)
    assert nested_diagnostic is not None and nested_diagnostic.observed == 2
    # The same atoms in sibling groups are independent.
    assert _incomplete("#if A\n#endif\n#if B\n#endif\n", max_atoms=1) is None


def test_elif_and_else_branches_are_one_component():
    chained = "#if A\n#elif B\n#else\n#endif\n"

    diagnostic = _incomplete(chained, max_atoms=1)

    assert diagnostic is not None and diagnostic.observed == 2
    assert _incomplete(chained, max_atoms=2) is None


def test_deeply_nested_conditionals_keep_parent_and_child_coupled():
    depth = 10
    source = "".join(f"#if N_{index}\n" for index in range(depth))
    source += "x;\n" + "#endif\n" * depth
    source += "".join(f"#if S_{index}\n#endif\n" for index in range(70))

    diagnostic = _incomplete(source, max_atoms=depth - 1)
    assert diagnostic is not None and diagnostic.observed == depth
    assert _incomplete(source, max_atoms=depth) is None


def test_nested_child_condition_is_still_analyzed_in_its_parents_context():
    source = "#if A\n#if A\nx;\n#endif\n#endif\n" + "".join(
        f"#if F_{index}\n#endif\n" for index in range(80)
    )

    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=1))

    assert result.complete
    assert [(f.kind, f.location.line) for f in result.findings] == [
        (cpre.FindingKind.REDUNDANT_BRANCH, 2)
    ]


# --- assumptions ----------------------------------------------------------------------


def test_assumptions_on_unrelated_macros_do_not_consume_the_atom_budget():
    assumptions = {f"UNUSED_{index}": index % 2 == 0 for index in range(100)}

    result = cpre.analyze_source("#if A\n#endif\n", assumptions=assumptions)

    assert result.complete


def test_assumed_findings_equal_findings_of_each_group_analyzed_alone():
    source = "".join(f"#if F_{index}\nx;\n#endif\n" for index in range(120))
    assumptions = {"F_5": False, "F_70": True, "F_119": False}

    result = cpre.analyze_source(source, assumptions=assumptions)

    assert result.complete
    assert [(f.kind, f.location.line, f.original_condition) for f in result.findings] == [
        (cpre.FindingKind.DEAD_BRANCH, 3 * 5 + 1, "F_5"),
        (cpre.FindingKind.REDUNDANT_BRANCH, 3 * 70 + 1, "F_70"),
        (cpre.FindingKind.DEAD_BRANCH, 3 * 119 + 1, "F_119"),
    ]
    assert all(finding.depends_on_assumptions for finding in result.findings)


def test_defined_and_value_atoms_of_one_macro_stay_in_one_component_under_assumptions():
    source = "#if defined(X)\n#endif\n#if X\n#endif\n"
    assumptions = {"UNRELATED": True}

    coupled = _incomplete_with(source, assumptions, max_atoms=1)
    assert coupled is not None
    assert (coupled.resource, coupled.limit, coupled.observed) == ("atoms", 1, 2)
    assert cpre.analyze_source(
        source, assumptions=assumptions, options=cpre.AnalysisOptions(max_atoms=2)
    ).complete


def test_defined_assumption_on_one_macro_reaches_every_group_that_mentions_it():
    source = "#if defined(X)\n#endif\n" + "".join(f"#if F_{i}\n#endif\n" for i in range(80))

    result = cpre.analyze_source(source, assumptions=cpre.MacroAssumptions(undefined={"X"}))

    assert result.complete
    assert [(f.kind, f.location.line) for f in result.findings] == [
        (cpre.FindingKind.DEAD_BRANCH, 1)
    ]


def test_context_that_is_unsatisfiable_in_one_component_makes_every_group_dead():
    source = "#if A\n#endif\n#if B\n#endif\n#if C\n#endif\n"
    # (A || B) && !A && !B is contradictory; C shares no atom with it.
    a, b = Variable("A"), Variable("B")
    contradiction = conjunction(disjunction(a, b), negate(a), negate(b))
    assert contradiction != FALSE

    tree = engine_analyze_source(source, assumptions=contradiction)

    statuses = [branch.analysis.status for group in tree.groups for branch in group.branches]
    assert statuses == ["dead", "dead", "dead"]


def test_constant_false_context_makes_every_group_dead():
    tree = engine_analyze_source("#if A\n#endif\n#if B\n#endif\n", assumptions=FALSE)

    statuses = [branch.analysis.status for group in tree.groups for branch in group.branches]
    assert statuses == ["dead", "dead"]


def test_engine_results_do_not_depend_on_how_groups_are_ordered_across_components():
    first = "#if A\n#if !A\n#endif\n#endif\n"
    second = "#if B || !B\n#endif\n"

    forward = engine_analyze_source(first + second)
    backward = engine_analyze_source(second + first)

    def analyses(tree):
        return {
            branch.expression_text: branch.analysis
            for group in tree.groups
            for branch in group.branches
        }

    assert analyses(forward) == analyses(backward)


# --- budget semantics -----------------------------------------------------------------


def _completes(source: str, max_work: int) -> bool:
    return cpre.analyze_source(source, options=cpre.AnalysisOptions(max_work=max_work)).complete


def _minimum_work(source: str) -> int:
    low, high = 1, 1
    while not _completes(source, high):
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if _completes(source, middle):
            high = middle
        else:
            low = middle + 1
    return low


def test_work_budget_is_global_not_per_component():
    one = _redundant_pair("F_0")
    many = "".join(_redundant_pair(f"F_{index}") for index in range(20))
    per_group = _minimum_work(one)

    # Twenty components never get twenty fresh budgets...
    assert not _completes(many, per_group)
    assert not _completes(many, per_group * 5)
    # ...and partitioning never costs more than analyzing the groups one after another.
    assert _completes(many, per_group * 20)


def test_work_exhaustion_is_incomplete_without_partial_findings_and_is_located():
    source = "".join(_redundant_pair(f"F_{index}") for index in range(30))

    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_work=100))

    assert not result.complete
    assert result.findings == ()
    diagnostic = result.incomplete[0]
    assert diagnostic.resource == "work"
    assert diagnostic.limit == 100
    assert diagnostic.location is not None and diagnostic.location.line > 1


def test_work_exhaustion_is_deterministic():
    source = "".join(_redundant_pair(f"F_{index}") for index in range(30))
    options = cpre.AnalysisOptions(max_work=100)

    assert (
        cpre.analyze_source(source, options=options).incomplete
        == cpre.analyze_source(source, options=options).incomplete
    )


def test_partitioning_never_uses_more_work_than_one_group_after_another():
    groups = [
        "#if A\n#elif A\ny;\n#endif\n",
        "#if B || !B\nx;\n#endif\n",
        "#if C\n#if !C\nz;\n#endif\n#endif\n",
    ]
    limits = ResourceLimits()
    spent = []
    for source in (*groups, "".join(groups)):
        budget = AnalysisBudget(limits.max_work)
        engine_analyze_source(source, limits=limits, budget=budget)
        spent.append(budget.work)

    assert spent[-1] <= sum(spent[:-1])


def test_bdd_node_limit_applies_per_component_manager():
    source = "#if A && B\n#endif\n#if C && D\n#endif\n"

    diagnostic = _incomplete(source, max_bdd_nodes=1)

    assert diagnostic is not None
    assert diagnostic.resource == "bdd_nodes"
    assert diagnostic.location == cpre.SourceLocation(line=1)
    # Each two-atom component fits comfortably once the cap allows a handful of nodes.
    assert _incomplete(source, max_bdd_nodes=8) is None


def test_atom_limit_diagnostic_reports_the_first_oversized_component_deterministically():
    script = """
import json
import cpre
source = '#if A\\n#endif\\n#if B && C && D\\n#endif\\n#if E && F && G && H\\n#endif\\n'
result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))
d = result.incomplete[0]
print(json.dumps([d.resource, d.limit, d.observed, d.location.line if d.location else None]))
"""
    outputs = []
    for seed in ("1", "7", "42"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs == [["atoms", 2, 3, None]] * 3


@pytest.mark.parametrize("count", [0, 1, 2])
def test_degenerate_sources_still_analyze(count):
    source = "#if 1\n#endif\n" * count + "plain();\n"

    assert cpre.analyze_source(source).complete
