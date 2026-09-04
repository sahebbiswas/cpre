"""Internal branch reachability and simplification analysis."""

from __future__ import annotations

from typing import Iterator, Sequence

from .expressions import conjunction, disjunction, expression_atoms_in_order, negate, simplify
from .model import (
    BooleanAtom,
    BranchAnalysis,
    ConditionalGroup,
    ConditionalTree,
    Expression,
    FALSE,
    TRUE,
)
from .parser import parse_source
from .robdd import BDD, exact_simplify, simplify_under


def tree_expressions(groups: Sequence[ConditionalGroup]) -> Iterator[Expression]:
    for group in groups:
        for branch in group.branches:
            if branch.expression is not None:
                yield branch.expression
            yield from tree_expressions(branch.children)


def analyze_tree(tree: ConditionalTree) -> ConditionalTree:
    atoms: list[BooleanAtom] = []
    seen_atoms: set[BooleanAtom] = set()
    for expression in tree_expressions(tree.groups):
        for atom in expression_atoms_in_order(expression):
            if atom not in seen_atoms:
                seen_atoms.add(atom)
                atoms.append(atom)
    bdd = BDD(atoms)

    def analyze_groups(groups: Sequence[ConditionalGroup], parent: Expression) -> None:
        for group in groups:
            covered: Expression = FALSE
            for branch in group.branches:
                available = conjunction(parent, negate(covered))
                condition = branch.expression if branch.expression is not None else TRUE
                effective = conjunction(available, condition)
                simplified = exact_simplify(condition, bdd) if branch.expression is not None else None
                contextual = (
                    simplify_under(condition, available, bdd)
                    if branch.expression is not None and bdd.satisfiable(available)
                    else simplified
                )
                if not bdd.satisfiable(parent):
                    status = "dead"
                    reason = "enclosing branch is unreachable"
                elif not bdd.satisfiable(available):
                    status = "dead"
                    reason = "earlier branch conditions cover every remaining case"
                elif not bdd.satisfiable(effective):
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


def analyze_source(source: str) -> ConditionalTree:
    return analyze_tree(parse_source(source))


__all__ = ["analyze_source", "analyze_tree", "tree_expressions"]
