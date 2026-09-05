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
from .robdd import AnalysisLimitExceeded, BDD, ResourceLimits, exact_simplify, simplify_under


def tree_expressions(groups: Sequence[ConditionalGroup]) -> Iterator[Expression]:
    for group in groups:
        for branch in group.branches:
            if branch.expression is not None:
                yield branch.expression
            yield from tree_expressions(branch.children)


def _macro_semantics(
    tree: ConditionalTree,
    *,
    legacy_symbolic: bool,
) -> Expression:
    """Return C preprocessor relationships between value and definedness atoms."""

    if legacy_symbolic:
        return TRUE

    names: set[str] = set()
    for expression in tree_expressions(tree.groups):
        for atom in expression_atoms_in_order(expression):
            if isinstance(atom, Variable) and not isinstance(atom, DefinedVariable):
                names.add(atom.name)
            elif isinstance(atom, DefinedVariable):
                names.add(atom.name)

    # An undefined macro evaluates to zero in a preprocessor expression. Thus a
    # truthy bare macro implies that the macro is defined; the converse is not
    # true because a defined macro may expand to zero.
    return conjunction(
        *(
            disjunction(negate(Variable(name)), DefinedVariable(name))
            for name in sorted(names)
        )
    )


def analyze_tree(
    tree: ConditionalTree,
    *,
    assumptions: Expression | None = None,
    limits: ResourceLimits | None = None,
) -> ConditionalTree:
    use_assumptions = assumptions is not None
    proof_context = conjunction(
        assumptions or TRUE,
        _macro_semantics(tree, legacy_symbolic=not use_assumptions),
    )

    atoms: list[BooleanAtom] = []
    seen_atoms: set[BooleanAtom] = set()
    expressions = list(tree_expressions(tree.groups))
    if use_assumptions:
        expressions.append(proof_context)
    for expression in expressions:
        for atom in expression_atoms_in_order(expression):
            if atom not in seen_atoms:
                seen_atoms.add(atom)
                atoms.append(atom)
    bdd = BDD(atoms, limits=limits)

    def satisfiable(expression: Expression) -> bool:
        if not use_assumptions:
            return bdd.satisfiable(expression)
        return bdd.satisfiable(conjunction(proof_context, expression))

    def analyze_groups(groups: Sequence[ConditionalGroup], parent: Expression) -> None:
        for group in groups:
            covered: Expression = FALSE
            for branch in group.branches:
                try:
                    available = conjunction(parent, negate(covered))
                    condition = branch.expression if branch.expression is not None else TRUE
                    effective = conjunction(available, condition)
                    simplified = (
                        simplify_under(condition, proof_context, bdd)
                        if branch.expression is not None
                        and use_assumptions
                        and bdd.satisfiable(proof_context)
                        else exact_simplify(condition, bdd)
                        if branch.expression is not None
                        else None
                    )

                    contextual_context = (
                        conjunction(proof_context, available)
                        if use_assumptions
                        else available
                    )
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
                except AnalysisLimitExceeded as error:
                    if error.line is None:
                        error.line = branch.line
                    raise

    analyze_groups(tree.groups, TRUE)
    return tree


def analyze_source(
    source: str,
    *,
    assumptions: Expression | None = None,
    limits: ResourceLimits | None = None,
) -> ConditionalTree:
    return analyze_tree(
        parse_source(source, distinguish_defined=assumptions is not None),
        assumptions=assumptions,
        limits=limits,
    )


__all__ = ["analyze_source", "analyze_tree", "tree_expressions"]
