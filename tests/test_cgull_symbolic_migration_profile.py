from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import cpre


PROFILE_PATH = Path(__file__).parent / "compatibility" / "cgull-symbolic-profile.json"


def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _require_satisfiable(expression) -> bool:
    result = cpre.satisfiable(expression)
    assert result.complete, result.incomplete
    assert result.satisfiable is not None
    return result.satisfiable


def _require_proof(result) -> bool:
    assert result.complete, result.incomplete
    assert result.holds is not None
    return result.holds


def _require_simplification(expression):
    result = cpre.exact_simplify(expression)
    assert result.complete, result.incomplete
    assert result.expression is not None
    return result.expression


def _expression_size(expression) -> int:
    if isinstance(expression, cpre.Negation):
        return 1 + _expression_size(expression.operand)
    if isinstance(expression, (cpre.Conjunction, cpre.Disjunction)):
        return 1 + sum(_expression_size(item) for item in expression.operands)
    return 1


def _simplify_under_context(expression, context):
    expression = _require_simplification(expression)

    if _require_proof(cpre.implies(context, expression)):
        return cpre.TRUE
    if _require_proof(cpre.implies(context, cpre.negate(expression))):
        return cpre.FALSE

    if isinstance(expression, cpre.Negation):
        candidate = _require_simplification(
            cpre.negate(_simplify_under_context(expression.operand, context))
        )
    elif isinstance(expression, cpre.Conjunction):
        operands = []
        for operand in expression.operands:
            if _require_proof(cpre.implies(context, operand)):
                continue
            if _require_proof(cpre.implies(context, cpre.negate(operand))):
                return cpre.FALSE
            operands.append(_simplify_under_context(operand, context))
        candidate = _require_simplification(cpre.conjunction(*operands))
    elif isinstance(expression, cpre.Disjunction):
        operands = []
        for operand in expression.operands:
            if _require_proof(cpre.implies(context, operand)):
                return cpre.TRUE
            if _require_proof(cpre.implies(context, cpre.negate(operand))):
                continue
            operands.append(_simplify_under_context(operand, context))
        candidate = _require_simplification(cpre.disjunction(*operands))
    else:
        candidate = expression

    equivalent = cpre.equivalent(
        cpre.conjunction(context, expression),
        cpre.conjunction(context, candidate),
    )
    return candidate if _require_proof(equivalent) else expression


def _branch_semantics(source: str):
    tree = cpre.parse_conditionals(source, filename="cgull-symbolic-fixture.c")
    results: list[dict[str, object]] = []

    def walk(block, parent_context) -> None:
        covered = cpre.FALSE
        for branch in block.branches:
            directive = branch.directive
            remaining = cpre.conjunction(parent_context, cpre.negate(covered))

            if directive.kind == "else":
                effective = remaining
                reachable = _require_satisfiable(effective)
                results.append(
                    {
                        "kind": directive.kind,
                        "offset": directive.source_range.start.offset,
                        "status": "unchanged" if reachable else "dead",
                        "reachability": "reachable" if reachable else "unreachable",
                        "condition": None,
                        "simplified": None,
                        "effective": effective,
                    }
                )
                if reachable:
                    for child in branch.children:
                        walk(child, effective)
                covered = cpre.TRUE
                continue

            condition = directive.condition
            if condition is None:
                results.append(
                    {
                        "kind": directive.kind,
                        "offset": directive.source_range.start.offset,
                        "status": "unchanged",
                        "reachability": "unknown",
                        "condition": directive.condition_text,
                        "simplified": None,
                        "effective": None,
                    }
                )
                return

            effective = cpre.conjunction(remaining, condition)
            reachable = _require_satisfiable(effective)
            status = "unchanged"
            simplified = None

            if not reachable:
                status = "dead"
                reachability = "unreachable"
            else:
                reachability = "reachable"
                if _require_proof(cpre.implies(remaining, condition)):
                    status = "redundant"
                else:
                    original_size = _expression_size(condition)
                    local = _require_simplification(condition)
                    contextual = _simplify_under_context(local, remaining)
                    candidates = [
                        item
                        for item in (local, contextual)
                        if item not in (cpre.TRUE, cpre.FALSE)
                    ]
                    if candidates:
                        candidate = min(
                            candidates,
                            key=lambda item: (
                                _expression_size(item),
                                cpre.format_expression(item),
                            ),
                        )
                        equivalent = cpre.equivalent(
                            cpre.conjunction(remaining, condition),
                            cpre.conjunction(remaining, candidate),
                        )
                        if (
                            _expression_size(candidate) < original_size
                            and _require_proof(equivalent)
                        ):
                            status = "simplified"
                            simplified = candidate

            results.append(
                {
                    "kind": directive.kind,
                    "offset": directive.source_range.start.offset,
                    "status": status,
                    "reachability": reachability,
                    "condition": directive.condition_text,
                    "simplified": (
                        cpre.format_expression(simplified) if simplified is not None else None
                    ),
                    "effective": effective,
                }
            )

            if reachable:
                for child in branch.children:
                    walk(child, effective)
            covered = cpre.disjunction(covered, condition)

    for block in tree.blocks:
        walk(block, cpre.TRUE)
    return tree, results


def _cgull_witness(assignment):
    kind_map = {
        cpre.WitnessAtomKind.MACRO_DEFINED: "defined",
        cpre.WitnessAtomKind.MACRO_VALUE: "macro_value",
        cpre.WitnessAtomKind.PREDICATE: "predicate",
    }
    return tuple((kind_map[item.kind], item.symbol, item.value) for item in assignment)


def test_symbolic_profile_is_pinned_and_inventory_is_explicit():
    profile = _profile()

    assert profile["schema_version"] == 1
    assert profile["downstream"] == {
        "repository": "sahebbiswas/cgull",
        "commit": "265f7bfdcb806a775a88510483ba28552f50f006",
        "validated_on": "2026-09-17",
    }
    assert profile["cpre"]["version"] == cpre.__version__ == "0.11.0"
    assert profile["cpre"]["implementation_commit"] == (
        "88e14949e594c1739467b40ea53210e56bb56771"
    )
    assert profile["cpre"]["first_suitable_release"] == "0.11.0"

    readiness = profile["readiness"]
    assert readiness["status"] == "ready"
    assert readiness["scope"] == "cgull_symbolic_preprocessor_migration"
    assert readiness["release_state"] == "candidate"
    assert readiness["published_release_ready"] is False
    assert readiness["complete_but_semantically_divergent_cases"] == 0
    assert readiness["accepted_differences"] == 2
    assert readiness["revalidate_on_downstream_revision_change"] is True

    assert profile["inventory"]["symbolic_modules"] == [
        "cgull/preprocessor/expressions.py",
        "cgull/preprocessor/directives.py",
        "cgull/preprocessor/robdd.py",
        "cgull/preprocessor/branch_analysis.py",
        "cgull/preprocessor/configuration_space.py",
        "cgull/preprocessor/profile_reduction.py",
    ]
    assert "cgull/rules/preprocessor_reachability.py" in profile["inventory"][
        "direct_consumers"
    ]
    assert "tests/test_config_profile_reduction.py" in profile["inventory"][
        "representative_tests"
    ]


def test_public_expression_surface_matches_cgull_semantics():
    value = cpre.Variable("FEATURE")
    defined = cpre.DefinedVariable("FEATURE")
    predicate = cpre.Predicate("FEATURE")

    assert len({value, defined, predicate}) == 3
    assert cpre.conjunction(value, cpre.negate(value)) == cpre.FALSE
    assert cpre.disjunction(value, cpre.negate(value)) == cpre.TRUE

    expression = cpre.Disjunction(
        (
            cpre.Negation(cpre.DefinedVariable("DISABLED")),
            cpre.Conjunction((cpre.Variable("B"), cpre.Variable("A"))),
        )
    )
    assert cpre.format_expression(expression) == "!defined(DISABLED) || A && B"

    structured = cpre.expression_to_dict(
        cpre.Conjunction((value, defined, cpre.Predicate("VERSION >= 4")))
    )
    assert {item["kind"] for item in structured["operands"]} == {
        "variable",
        "defined",
        "predicate",
    }
    assert cpre.expression_from_dict(structured) == cpre.normalize(
        cpre.Conjunction((value, defined, cpre.Predicate("VERSION >= 4")))
    )


def test_lossless_structure_covers_cgull_ranges_c23_and_recovery():
    source = (
        'char *literal = "#if NOT_A_DIRECTIVE";\n'
        "#if ROOT && \\\n"
        "    OTHER\n"
        "root\n"
        "#ifdef CHILD\n"
        "child\n"
        "#elifdef ALT\n"
        "alt\n"
        "#elifndef FALLBACK\n"
        "fallback\n"
        "#else\n"
        "last\n"
        "#endif\n"
        "#endif\n"
        "#ifndef DISABLED\n"
        "enabled\n"
        "#endif\n"
    )
    tree = cpre.parse_conditionals(source, filename="ranges.c")

    assert not tree.diagnostics
    assert [item.kind for item in tree.directives] == [
        "if",
        "ifdef",
        "elifdef",
        "elifndef",
        "else",
        "endif",
        "endif",
        "ifndef",
        "endif",
    ]
    outer = tree.blocks[0]
    assert outer.branches[0].directive.condition_range.text(source) == (
        "ROOT && \\\n    OTHER"
    )
    assert outer.branches[0].directive.logical_condition.split() == ["ROOT", "&&", "OTHER"]
    nested = outer.branches[0].children[0]
    assert nested.branches[0].body_range.text(source) == "child\n"
    assert nested.source_range.text(source).startswith("#ifdef CHILD\n")
    assert nested.source_range.text(source).endswith("#endif\n")
    assert tree.blocks[1].branches[0].directive.condition == cpre.Negation(
        cpre.DefinedVariable("DISABLED")
    )

    malformed = cpre.parse_conditionals("#if A\n#else\n#else\n#elif B\n#endif\n")
    assert [item.code for item in malformed.diagnostics] == [
        cpre.StructureDiagnosticCode.DUPLICATE_ELSE,
        cpre.StructureDiagnosticCode.BRANCH_AFTER_ELSE,
    ]
    assert len(malformed.directives) == 5


def test_structure_diagnostic_name_differences_are_explicitly_mapped():
    profile = _profile()
    mapping = next(
        item["cgull_to_cpre"]
        for item in profile["accepted_differences"]
        if item["id"] == "structure-diagnostic-code-names"
    )
    assert mapping == {
        "invalid_macro": "malformed_macro_directive",
        "unexpected_tokens": "trailing_directive_text",
        "misplaced_directive": "unmatched_directive",
        "unterminated_block": "unterminated_conditional",
    }

    fixtures = {
        "invalid_macro": "#ifdef 23\n#endif\n",
        "unexpected_tokens": "#if A\n#else bad\n#endif\n",
        "misplaced_directive": "#elif A\n",
        "unterminated_block": "#if A\nbody\n",
    }
    for cgull_code, source in fixtures.items():
        tree = cpre.parse_conditionals(source)
        assert tree.diagnostics
        assert tree.diagnostics[0].code.value == mapping[cgull_code]
        assert tree.diagnostics[0].source_range.text(source)


def test_exact_proofs_witnesses_and_limits_match_cgull_needs():
    a = cpre.Variable("A")
    b = cpre.Variable("B")
    hard = cpre.Disjunction(
        (
            cpre.Conjunction((a, b)),
            cpre.Conjunction((a, cpre.Negation(b))),
        )
    )

    assert _require_satisfiable(a)
    assert not _require_satisfiable(cpre.conjunction(a, cpre.negate(a)))
    assert _require_proof(cpre.implies(cpre.conjunction(a, b), a))
    assert not _require_proof(cpre.implies(a, b))
    assert _require_proof(cpre.equivalent(hard, a))
    assert _require_simplification(hard) == a

    expression = cpre.Conjunction(
        (
            cpre.DefinedVariable("FEATURE"),
            cpre.Negation(cpre.Variable("FEATURE")),
            cpre.Predicate("VERSION >= 4"),
        )
    )
    witness = cpre.witness_assignment(expression)
    assert witness.complete and witness.satisfiable is True
    assert witness.assignment is not None
    assert _cgull_witness(witness.assignment) == (
        ("defined", "FEATURE", True),
        ("predicate", "VERSION >= 4", True),
        ("macro_value", "FEATURE", False),
    )

    unsat = cpre.witness_assignment(cpre.conjunction(a, cpre.negate(a)))
    incomplete = cpre.witness_assignment(
        cpre.conjunction(a, b),
        options=cpre.AnalysisOptions(max_atoms=1),
    )
    assert unsat.complete and unsat.satisfiable is False and unsat.assignment is None
    assert not incomplete.complete
    assert incomplete.satisfiable is None and incomplete.assignment is None
    assert incomplete.incomplete is not None
    assert (incomplete.incomplete.resource, incomplete.incomplete.limit) == ("atoms", 1)


def test_representative_cgull_branch_analysis_is_semantically_equivalent():
    source = (
        "#if ROOT\n"
        "#if ROOT && CHILD\n"
        "child\n"
        "#endif\n"
        "#if ROOT\n"
        "redundant\n"
        "#endif\n"
        "#endif\n"
        "#if A\n"
        "first\n"
        "#elif A\n"
        "dead\n"
        "#else\n"
        "fallback\n"
        "#endif\n"
    )
    tree, analyses = _branch_semantics(source)

    assert not tree.diagnostics
    assert [(item["kind"], item["status"], item["reachability"]) for item in analyses] == [
        ("if", "unchanged", "reachable"),
        ("if", "simplified", "reachable"),
        ("if", "redundant", "reachable"),
        ("if", "unchanged", "reachable"),
        ("elif", "dead", "unreachable"),
        ("else", "unchanged", "reachable"),
    ]
    assert analyses[1]["simplified"] == "CHILD"
    assert [item["offset"] for item in analyses] == sorted(item["offset"] for item in analyses)


def test_representative_cgull_configuration_witness_uses_effective_context():
    source = (
        "#ifdef FEATURE\n"
        "#if !FEATURE && VERSION >= 4\n"
        "enabled\n"
        "#endif\n"
        "#endif\n"
    )
    tree, analyses = _branch_semantics(source)
    assert not tree.diagnostics
    assert len(analyses) == 2

    effective = analyses[1]["effective"]
    witness = cpre.witness_assignment(effective)
    assert witness.complete and witness.satisfiable is True
    assert witness.assignment is not None
    assert _cgull_witness(witness.assignment) == (
        ("defined", "FEATURE", True),
        ("predicate", "VERSION >= 4", True),
        ("macro_value", "FEATURE", False),
    )


def test_witness_output_is_hash_seed_independent_through_public_package():
    script = """
import json
import cpre
expr = cpre.Conjunction((
    cpre.DefinedVariable('FEATURE'),
    cpre.Negation(cpre.Variable('FEATURE')),
    cpre.Predicate('VERSION >= 4'),
))
result = cpre.witness_assignment(expr)
print(json.dumps([(item.kind.value, item.symbol, item.value) for item in result.assignment]))
"""
    outputs = []
    for seed in ("1", "7", "101"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outputs.append(
            subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
        )
    assert len(set(outputs)) == 1
