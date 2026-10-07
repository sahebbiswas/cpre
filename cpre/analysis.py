"""Internal branch reachability and simplification analysis."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .expressions import conjunction, disjunction, expression_atoms_in_order, negate, simplify
from .model import (
    FALSE,
    TRUE,
    BooleanAtom,
    BranchAnalysis,
    ConditionalGroup,
    ConditionalTree,
    Conjunction,
    DefinedVariable,
    Expression,
    Variable,
)
from .parser import parse_source
from .robdd import (
    BDD,
    AnalysisBudget,
    AnalysisLimitExceeded,
    ResourceLimits,
    exact_simplify,
    simplify_under,
)


def tree_expressions(groups: Sequence[ConditionalGroup]) -> Iterator[Expression]:
    for group in groups:
        for branch in group.branches:
            if branch.expression is not None:
                yield branch.expression
            yield from tree_expressions(branch.children)


def _macro_semantics(tree: ConditionalTree, *, legacy_symbolic: bool) -> Expression:
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
    return conjunction(
        *(disjunction(negate(Variable(name)), DefinedVariable(name)) for name in sorted(names))
    )


def _top_level_conjuncts(expression: Expression) -> list[Expression]:
    expression = simplify(expression)
    if expression == TRUE:
        return []
    if isinstance(expression, Conjunction):
        return list(expression.operands)
    return [expression]


class _AtomPartition:
    """Union-find over Boolean atoms; atoms that interact end up in one component."""

    def __init__(self) -> None:
        self._parent: dict[BooleanAtom, BooleanAtom] = {}

    def find(self, atom: BooleanAtom) -> BooleanAtom:
        root = self._parent.setdefault(atom, atom)
        while root != self._parent[root]:
            root = self._parent[root]
        while atom != root:
            self._parent[atom], atom = root, self._parent[atom]
        return root

    def union_all(self, atoms: Sequence[BooleanAtom]) -> None:
        if not atoms:
            return
        first = self.find(atoms[0])
        for atom in atoms[1:]:
            other = self.find(atom)
            if other != first:
                self._parent[other] = first


@dataclass
class _Component:
    """One independent Boolean subproblem with its own BDD manager."""

    atoms: list[BooleanAtom]
    clauses: list[Expression]
    bdd: BDD | None = None
    context: Expression = TRUE


@dataclass(frozen=True)
class _Scope:
    """What a top-level conditional group is analyzed against."""

    bdd: BDD
    context: Expression
    use_assumptions: bool
    context_satisfiable: bool

    def satisfiable(self, expression: Expression) -> bool:
        if not self.use_assumptions:
            return self.bdd.satisfiable(expression)
        return self.context_satisfiable and self.bdd.satisfiable(
            conjunction(self.context, expression)
        )


def _unique_atoms(expressions: Sequence[Expression]) -> list[BooleanAtom]:
    return list(
        dict.fromkeys(
            atom for expression in expressions for atom in expression_atoms_in_order(expression)
        )
    )


def analyze_tree(
    tree: ConditionalTree,
    *,
    assumptions: Expression | None = None,
    limits: ResourceLimits | None = None,
    budget: AnalysisBudget | None = None,
) -> ConditionalTree:
    """Analyze every branch of ``tree``.

    Boolean reasoning is partitioned into independent components. Each
    top-level conditional group (with its ``#elif``/``#else`` branches and every
    nested conditional) is one unit, because branch reachability couples a
    group's branches to each other and children to their parents. Units that
    share a Boolean atom, directly or through the assumptions or the
    ``value -> defined`` macro semantics, are merged into one component. Every
    component gets its own BDD manager, so ``limits.max_atoms`` and
    ``limits.max_bdd_nodes`` apply per component, while ``budget`` (the work
    cap) stays shared by all components. Atom order inside a component is the
    source's first-occurrence order, so results match a single file-wide BDD.
    """
    use_assumptions = assumptions is not None
    resolved_limits = limits or ResourceLimits()
    resolved_budget = budget or AnalysisBudget(resolved_limits.max_work)

    clauses: list[Expression] = []
    if use_assumptions:
        # The simplified context decides which atoms exist and in what order, exactly
        # as a single file-wide analysis would; its conjuncts are the independent
        # constraints that get routed to components below.
        clauses = _top_level_conjuncts(
            conjunction(assumptions or TRUE, _macro_semantics(tree, legacy_symbolic=False))
        )

    # Global first-occurrence order: tree expressions first, then context atoms.
    atoms = _unique_atoms([*tree_expressions(tree.groups), *clauses])

    partition = _AtomPartition()
    group_atoms = [_unique_atoms(list(tree_expressions([group]))) for group in tree.groups]
    for members in group_atoms:
        partition.union_all(members)
    clause_atoms = [_unique_atoms([clause]) for clause in clauses]
    for members in clause_atoms:
        partition.union_all(members)

    components: dict[BooleanAtom, _Component] = {}
    for atom in atoms:
        root = partition.find(atom)
        components.setdefault(root, _Component([], [])).atoms.append(atom)
    context_satisfiable = True
    for clause, members in zip(clauses, clause_atoms):
        if members:
            components[partition.find(members[0])].clauses.append(clause)
        elif clause == FALSE:
            context_satisfiable = False
    for component in components.values():
        component.bdd = BDD(component.atoms, limits=resolved_limits, budget=resolved_budget)
        component.context = conjunction(*component.clauses)

    if use_assumptions and tree.groups:
        try:
            for component in components.values():
                assert component.bdd is not None
                if component.clauses and not component.bdd.satisfiable(component.context):
                    context_satisfiable = False
        except AnalysisLimitExceeded as error:
            if error.line is None:
                error.line = tree.groups[0].branches[0].line
            raise

    atom_free_bdd: BDD | None = None

    def scope_for(index: int) -> _Scope:
        nonlocal atom_free_bdd
        members = group_atoms[index]
        if members:
            component = components[partition.find(members[0])]
            assert component.bdd is not None
            return _Scope(component.bdd, component.context, use_assumptions, context_satisfiable)
        if atom_free_bdd is None:
            atom_free_bdd = BDD([], limits=resolved_limits, budget=resolved_budget)
        return _Scope(atom_free_bdd, TRUE, use_assumptions, context_satisfiable)

    def analyze_groups(
        groups: Sequence[ConditionalGroup], parent: Expression, scope: _Scope
    ) -> None:
        bdd = scope.bdd
        for group in groups:
            covered: Expression = FALSE
            for branch in group.branches:
                try:
                    available = conjunction(parent, negate(covered))
                    condition = branch.expression if branch.expression is not None else TRUE
                    effective = conjunction(available, condition)
                    simplified = (
                        simplify_under(condition, scope.context, bdd)
                        if branch.expression is not None
                        and use_assumptions
                        and scope.context_satisfiable
                        else exact_simplify(condition, bdd)
                        if branch.expression is not None
                        else None
                    )
                    contextual_context = (
                        conjunction(scope.context, available) if use_assumptions else available
                    )
                    contextual = (
                        simplify_under(condition, contextual_context, bdd)
                        if branch.expression is not None and scope.satisfiable(available)
                        else simplified
                    )
                    if not scope.satisfiable(parent):
                        status, reason = "dead", "enclosing branch is unreachable"
                    elif not scope.satisfiable(available):
                        status, reason = (
                            "dead",
                            "earlier branch conditions cover every remaining case",
                        )
                    elif not scope.satisfiable(effective):
                        status, reason = (
                            "dead",
                            "condition contradicts its parent or earlier branches",
                        )
                    elif branch.expression is not None and contextual == TRUE:
                        status, reason = (
                            "redundant",
                            "condition is always true in this branch context",
                        )
                    else:
                        status, reason = "reachable", None
                    branch.analysis = BranchAnalysis(
                        status=status,
                        simplified=simplified,
                        contextual=contextual,
                        effective=simplify(effective),
                        reason=reason,
                    )
                    analyze_groups(branch.children, effective, scope)
                    covered = TRUE if branch.expression is None else disjunction(covered, condition)
                except AnalysisLimitExceeded as error:
                    if error.line is None:
                        error.line = branch.line
                    raise

    for index, group in enumerate(tree.groups):
        analyze_groups([group], TRUE, scope_for(index))
    return tree


def analyze_source(
    source: str,
    *,
    assumptions: Expression | None = None,
    limits: ResourceLimits | None = None,
    budget: AnalysisBudget | None = None,
) -> ConditionalTree:
    return analyze_tree(
        parse_source(source, distinguish_defined=assumptions is not None),
        assumptions=assumptions,
        limits=limits,
        budget=budget,
    )


__all__ = ["analyze_source", "analyze_tree", "tree_expressions"]
