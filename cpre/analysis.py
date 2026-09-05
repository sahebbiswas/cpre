"""Internal branch reachability and simplification analysis."""

from __future__ import annotations

from typing import Iterator, Sequence

from .expressions import (
    conjunction,
    disjunction,
    expression_atoms_in_order,
    negate,
    simplify,
)
from .model import (
    BooleanAtom,
    BranchAnalysis,
    ConditionalGroup,
    ConditionalTree,
    DefinedVariable,
    Expression,
    FALSE,
    TRUE,
    Variable,
)
from .parser import parse_source
from .robdd import BDD, exact_simplify, simplify_under


def tree_expressions(groups: Sequence[ConditionalGroup]) -> Iterator[Expression]:
    for group in groups:
        for branch in group.branches:
            if branch.expression is not None:
                yield branch.expression
            yield from tree_expressions(branch.children)


def _macro_semantics(tree: ConditionalTree, *, legacy_symbolic: bool) -> Expression:
    """Return Boolean relationships between macro value and definedness atoms."""

    names: set[str] = set()
    for expression in tree_expressions(tree.groups):
        for atom in expression_atoms_in_order(expression):
            if isinstance(atom, Variable) and not isinstance(atom, DefinedVariable):
                names.add(atom.name)
            elif isinstance(atom, DefinedVariable):
                names.add(atom.name)

    constraints: list[Expression] = []
    for name in sorted(names):
        value = Variable(name)
        defined = DefinedVariable(name)
        constraints.append(disjunction(negate(value), defined))
        if legacy_symbolic:
            constraints.append(disjunction(negate(defined), value))
    return conjunction(*constraints)


def analyze_tree(
    tree: ConditionalTree,
    *,
    assumptions: Expression | None = None,
) -> ConditionalTree:
    assumption_expression = assumptions or TRUE
    proof_context = conjunction(
        assumption_expression,
        _macro_semantics(tree, legacy_symbolic=assumptions is None),
    )

    atoms: list[BooleanAtom] = []
    seen_atoms: set[BooleanAtom] = set()
    for expression in (*tree_expressions(tree.groups), proof_context):
        for atom in expression_atoms_in_order(expression):
            if atom not in seen_atoms:
                seen_atoms.add(atom)
                atoms.append(atom)
    bdd = BDD(atoms)

    def satisfiable(expression: Expression) -> bool:
        return bdd.satisfiable(conjunction(proof_context, expression))

    def analyze_groups(groups: Sequence[ConditionalGroup], parent: Expression) -> None:
        for group in groups:
            covered: Expression = FALSE
            for branch in group.branches:
                available = conjunction(parent, negate(covered))
                condition = branch.expression if branch.expression is not None else TRUE
                effective = conjunction(available, condition)
                simplified = (
                    simplify_under(condition, proof_context, bdd)
                    if branch.expression is not None and bdd.satisfiable(proof_context)
                    else exact_simplify(condition, bdd) if branch.expression is not None else None
                )
                contextual_context = conjunction(proof_context, available)
                contextual = (
                    simplify_under(condition, contextual_context, bdd)
                    if branch.expression is not None and bdd.satisfiable(contextual_context)
                    else simplified
                )
                if not satisfiable(parent):
                    status = "dead"
                    reason = "enclosing branch is unreachable"
                elif not satisfiable(available):
                    status = "dead"
                    reason = "earlier branch conditions cover every remaining case"
                elif not satisfiable(effective):
                    status = "dead"
                    reason = "condition contradicts its parent or earlier branches"
                elif branch.expression is not None and contextual == TRUE:
                    status = "redundant"
                    reason = "condition is always true in this branch context"
                else:
                    status = "reachable"
                    reason = None
                branch.analysis = BranchAnalysis(
                    status=status,
                    simplified=simplified,
                    contextual=contextual,
                    effective=simplify(effective),
                    reason=reason,
                )
                analyze_groups(branch.children, effective)
                covered = TRUE if branch.expression is None else disjunction(covered, condition)

    analyze_groups(tree.groups, TRUE)
    return tree


def analyze_source(
    source: str,
    *,
    assumptions: Expression | None = None,
) -> ConditionalTree:
    return analyze_tree(
        parse_source(source, distinguish_defined=assumptions is not None),
        assumptions=assumptions,
    )


__all__ = ["analyze_source", "analyze_tree", "tree_expressions"]
