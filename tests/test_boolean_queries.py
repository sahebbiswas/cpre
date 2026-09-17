import json
import os
import subprocess
import sys

import cpre


def _hard_expression():
    a = cpre.Variable("A")
    b = cpre.Variable("B")
    return cpre.Disjunction(
        (
            cpre.Conjunction((a, b)),
            cpre.Conjunction((a, cpre.Negation(b))),
        )
    )


def test_satisfiability_distinguishes_tautology_and_contradiction():
    a = cpre.Variable("A")

    tautology = cpre.satisfiable(cpre.Disjunction((a, cpre.Negation(a))))
    contradiction = cpre.satisfiable(cpre.Conjunction((a, cpre.Negation(a))))

    assert tautology.complete and tautology.satisfiable is True
    assert contradiction.complete and contradiction.satisfiable is False


def test_implication_and_equivalence_report_true_and_false_proofs():
    a = cpre.Variable("A")
    b = cpre.Variable("B")

    assert cpre.implies(cpre.Conjunction((a, b)), a).holds is True
    assert cpre.implies(a, b).holds is False
    assert cpre.equivalent(_hard_expression(), a).holds is True
    assert cpre.equivalent(a, b).holds is False


def test_exact_simplification_returns_only_proven_rewrite():
    result = cpre.exact_simplify(_hard_expression())

    assert result.complete
    assert result.expression == cpre.Variable("A")


def test_witness_preserves_macro_value_definedness_and_predicate_categories():
    expression = cpre.Conjunction(
        (
            cpre.Variable("FEATURE"),
            cpre.Negation(cpre.DefinedVariable("FEATURE")),
            cpre.Predicate("VERSION >= 4"),
        )
    )

    result = cpre.witness_assignment(expression)

    assert result.complete and result.satisfiable is True
    assert result.assignment == (
        cpre.WitnessAssignment(cpre.WitnessAtomKind.MACRO_DEFINED, "FEATURE", False),
        cpre.WitnessAssignment(cpre.WitnessAtomKind.PREDICATE, "VERSION >= 4", True),
        cpre.WitnessAssignment(cpre.WitnessAtomKind.MACRO_VALUE, "FEATURE", True),
    )


def test_witness_fills_dont_care_atoms_with_false():
    a = cpre.Variable("A")
    result = cpre.witness_assignment(cpre.Disjunction((a, cpre.Negation(a))))

    assert result.satisfiable is True
    assert result.assignment == (
        cpre.WitnessAssignment(cpre.WitnessAtomKind.MACRO_VALUE, "A", False),
    )


def test_witness_distinguishes_unsat_from_incomplete():
    a = cpre.Variable("A")
    unsat = cpre.witness_assignment(cpre.Conjunction((a, cpre.Negation(a))))
    incomplete = cpre.witness_assignment(
        cpre.Conjunction((a, cpre.Variable("B"))),
        options=cpre.AnalysisOptions(max_atoms=1),
    )

    assert unsat.complete and unsat.satisfiable is False and unsat.assignment is None
    assert not incomplete.complete
    assert incomplete.satisfiable is None and incomplete.assignment is None
    assert incomplete.incomplete is not None
    assert incomplete.incomplete.resource == "atoms"
    assert incomplete.incomplete.limit == 1
    assert incomplete.incomplete.observed == 2


def test_node_and_work_limits_are_structured_and_never_become_false_proofs():
    a = cpre.Variable("A")
    b = cpre.Variable("B")
    expression = cpre.Conjunction((a, b))

    node_limited = cpre.satisfiable(
        expression,
        options=cpre.AnalysisOptions(max_bdd_nodes=1),
    )
    work_limited = cpre.implies(
        a,
        b,
        options=cpre.AnalysisOptions(max_work=1),
    )
    simplify_limited = cpre.exact_simplify(
        expression,
        options=cpre.AnalysisOptions(max_work=1),
    )

    assert not node_limited.complete and node_limited.satisfiable is None
    assert node_limited.incomplete is not None
    assert node_limited.incomplete.resource == "bdd_nodes"
    assert not work_limited.complete and work_limited.holds is None
    assert work_limited.incomplete is not None
    assert work_limited.incomplete.resource == "work"
    assert not simplify_limited.complete and simplify_limited.expression is None


def test_representative_cgull_branch_and_configuration_proofs():
    root = cpre.Variable("ROOT")
    child = cpre.Variable("CHILD")
    feature_value = cpre.Variable("FEATURE")
    feature_defined = cpre.DefinedVariable("FEATURE")

    assert cpre.implies(cpre.Conjunction((root, child)), root).holds is True
    assert cpre.equivalent(_hard_expression(), cpre.Variable("A")).holds is True

    profile = cpre.witness_assignment(
        cpre.Conjunction((feature_defined, cpre.Negation(feature_value)))
    )
    assert profile.satisfiable is True
    assert profile.assignment == (
        cpre.WitnessAssignment(cpre.WitnessAtomKind.MACRO_DEFINED, "FEATURE", True),
        cpre.WitnessAssignment(cpre.WitnessAtomKind.MACRO_VALUE, "FEATURE", False),
    )


def test_witness_is_deterministic_across_hash_seeds():
    script = """
import json
import cpre
A = cpre.Variable('A')
D = cpre.DefinedVariable('A')
P = cpre.Predicate('A')
expr = cpre.Disjunction((cpre.Conjunction((A, D)), cpre.Conjunction((A, P))))
result = cpre.witness_assignment(expr)
print(json.dumps([(item.kind.value, item.symbol, item.value) for item in result.assignment]))
"""
    outputs = []
    for seed in ("1", "7", "101"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outputs.append(subprocess.check_output([sys.executable, "-c", script], env=env, text=True))

    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])
