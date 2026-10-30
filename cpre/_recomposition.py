"""Choosing the shortest equivalent replacement for a macro (the recomposition stage).

Simplifying a macro with the ROBDD is a one-way *decomposition*: the diagram is read back
as an expression over the atoms it was built from, and nothing guarantees that expression
is shorter than the replacement it came from. With ``expand_macros`` it is often longer,
because the atoms are the names the referenced macros expand to, not the macros
themselves. This module adds the opposite direction. It gathers several equivalent forms
of one macro, *recomposes* them from the macros the definition already refers to, and keeps
the one with the fewest tokens, and only when that is fewer than the definition has.

The forms, from the cheapest to produce:

* the expression the ROBDD gave for the order the atoms first appear in (the baseline);
* the expressions it gives for a few other atom orders, because the shape of the result
  depends on the order and a different one is sometimes shorter;
* each of those with sub-expressions folded back into the macros they came from: a
  sub-expression whose function equals a referenced macro's, or its negation, becomes that
  macro's name, and so does a group of operands of one ``&&`` or ``||`` that together
  equal it (``B && C && D`` is ``E && D`` when ``E`` is ``(B && C)``).

A form is kept only when it is proven equivalent, so every one that is reported is exact.
Everything here is best effort: a resource limit reached while looking for a shorter form
leaves the baseline in place, and never makes a macro incomplete.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from .expressions import (
    expression_atoms,
    expression_atoms_in_order,
    expressions_differ,
    format_expression,
    negate,
    tokens,
)
from .model import (
    TRUE,
    BooleanAtom,
    Conjunction,
    Constant,
    Disjunction,
    Expression,
    Negation,
    Predicate,
    SymbolicLiteral,
    Variable,
)
from .robdd import (
    BDD,
    AnalysisBudget,
    AnalysisLimitExceeded,
    ResourceLimits,
    exact_simplify,
)

# Other atom orders are tried only for expressions in this range; each costs a diagram.
# Fewer than three atoms have no other shape worth trying, and past eight the diagrams
# cost several times the analysis for almost no shorter forms.
MIN_REORDER_ATOMS = 3
MAX_REORDER_ATOMS = 8
MAX_REORDER_NODES = 64
# Operands of one ``&&`` / ``||`` are grouped by trying subsets, so their number is bounded.
MAX_GROUPED_OPERANDS = 8
# Work allowed for the search for a shorter form, apart from the budget of the analysis.
SEARCH_WORK = 20_000


def cost(expression: Expression) -> int:
    """The tokens of ``expression`` as a parenthesized replacement list.

    A lone identifier is counted as written. The ``!!`` that keeps its value 0 or 1 is
    required whatever form is chosen, so it takes no part in choosing between them.
    """
    return len(tokens(f"({format_expression(expression)})"))


def keeps_value(expression: Expression) -> Expression:
    """``expression`` as emitted: a lone identifier becomes ``!!X``, which stays 0 or 1.

    An identifier can hold any value, but every candidate macro is 0 or 1, so a form that
    is a lone identifier must not lose that. A symbolic literal is already 0 or 1.
    """
    if isinstance(expression, Variable) and not isinstance(expression, SymbolicLiteral):
        return Negation(Negation(expression))
    return expression


@dataclass(frozen=True)
class Expansion:
    """What the stage may use of the macros ``expand_macros`` substituted."""

    vocabulary: Callable[[], Mapping[str, Expression]]
    """Each referenced macro's name and its replacement, expanded down to the atoms."""
    expand: Callable[[Expression], Expression]
    """A form that names macros, with those names replaced by what they stand for."""


@dataclass(frozen=True)
class Choice:
    """The form chosen for a macro."""

    expression: Expression
    depends_on_expansion: bool
    """False when the form is equivalent whatever the referenced macros stand for."""


@dataclass
class _Candidate:
    expression: Expression
    depends_on_expansion: bool
    proven: bool | None = None  # None until it has been checked


def _orders(atoms: Sequence[BooleanAtom], expression: Expression) -> list[list[BooleanAtom]]:
    """A few atom orders other than the order of first appearance."""
    first = list(atoms)
    frequency = Counter(expression_atoms_in_order(expression))
    orders = [
        first[::-1],
        sorted(first, key=lambda atom: (-frequency[atom], first.index(atom))),
        sorted(first, key=lambda atom: (frequency[atom], first.index(atom))),
    ]
    unique: list[list[BooleanAtom]] = []
    for order in orders:
        if order != first and order not in unique:
            unique.append(order)
    return unique


def _other_orders(expression: Expression, bdd: BDD, limits: ResourceLimits) -> list[Expression]:
    """The ROBDD's expression for ``expression`` under other atom orders."""
    atoms = bdd.atoms
    if not MIN_REORDER_ATOMS <= len(atoms) <= MAX_REORDER_ATOMS:
        return []
    if bdd.node_count(bdd.build(expression)) > MAX_REORDER_NODES:
        return []
    budget = AnalysisBudget(min(limits.max_work, SEARCH_WORK))
    forms: list[Expression] = []
    for order in _orders(atoms, expression):
        try:
            forms.append(exact_simplify(expression, BDD(order, limits=limits, budget=budget)))
        except AnalysisLimitExceeded:
            break
    return forms


def _named_nodes(vocabulary: Mapping[str, Expression], bdd: BDD) -> dict[int, Expression]:
    """Each referenced macro's function, and its negation, as the expression naming it."""
    known = set(bdd.atoms)
    named: dict[int, Expression] = {}
    for name, body in vocabulary.items():
        if not expression_atoms(body) <= known:
            continue
        node = bdd.build(body)
        if node < 2:
            continue
        named.setdefault(node, Variable(name))
        named.setdefault(bdd.negate(node), Negation(Variable(name)))
    return named


def _fold(expression: Expression, named: Mapping[int, Expression], bdd: BDD) -> Expression:
    """``expression`` with its sub-expressions replaced by shorter macro names that equal them."""
    if isinstance(expression, (Constant, Variable, Predicate)):
        return expression
    hit = named.get(bdd.build(expression))
    if hit is not None and cost(hit) < cost(expression):
        return hit
    if isinstance(expression, Negation):
        return negate(_fold(expression.operand, named, bdd))
    constructor = type(expression)
    operands = [_fold(operand, named, bdd) for operand in expression.operands]
    nodes = [bdd.build(operand) for operand in expression.operands]
    if len(operands) <= MAX_GROUPED_OPERANDS:
        operands = _group(constructor, operands, nodes, named, bdd)
    return operands[0] if len(operands) == 1 else constructor(tuple(operands))


def _group(
    constructor: type[Conjunction] | type[Disjunction],
    operands: list[Expression],
    nodes: list[int],
    named: Mapping[int, Expression],
    bdd: BDD,
) -> list[Expression]:
    """Replace operands that together equal a macro's function with that macro's name."""
    operation = "and" if constructor is Conjunction else "or"
    while len(operands) > 2:
        best: tuple[int, tuple[int, ...], Expression, int] | None = None
        for size in range(len(operands) - 1, 1, -1):
            for subset in combinations(range(len(operands)), size):
                node = nodes[subset[0]]
                for index in subset[1:]:
                    node = bdd.apply(operation, node, nodes[index])
                hit = named.get(node)
                if hit is None:
                    continue
                saved = cost(constructor(tuple(operands[i] for i in subset))) - cost(hit)
                if saved > 0 and (best is None or saved > best[0]):
                    best = (saved, subset, hit, node)
        if best is None:
            break
        _, subset, hit, node = best
        kept = [i for i in range(len(operands)) if i not in subset]
        operands = [operands[i] for i in kept] + [hit]
        nodes = [nodes[i] for i in kept] + [node]
    return operands


def _equivalent(left: Expression, right: Expression, bdd: BDD) -> bool:
    try:
        return bdd.equivalent_under(TRUE, left, right)
    except (AnalysisLimitExceeded, KeyError):
        return False


def choose_replacement(
    *,
    original: Expression,
    original_cost: int,
    expanded: Expression,
    baseline: Expression,
    baseline_proven: bool,
    bdd: BDD,
    limits: ResourceLimits,
    expansion: Expansion | None,
) -> Choice | None:
    """The shortest proven-equivalent form that has fewer tokens than the definition.

    ``original`` is the definition's expression and ``original_cost`` the tokens of its
    replacement list (counted with outer parentheses). ``expanded`` is what the ROBDD
    ``bdd`` was built from, ``baseline`` the expression it gave for it and
    ``baseline_proven`` whether that was checked equivalent. ``expansion`` is ``None``
    when no macro was expanded, in which case ``expanded`` is ``original``.

    Returns ``None`` when nothing is shorter than the definition.
    """
    candidates = [_Candidate(baseline, expansion is not None, baseline_proven)]
    try:
        _gather(candidates, original, expanded, bdd, limits, expansion)
    except AnalysisLimitExceeded:
        pass  # keep what was found; the baseline is there regardless
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (cost(item[1].expression), item[1].depends_on_expansion, item[0]),
    )
    for _, candidate in ranked:
        if cost(candidate.expression) >= original_cost:
            return None
        if not expressions_differ(original, keeps_value(candidate.expression)):
            continue
        if candidate.proven is None:
            candidate.proven = _prove(candidate, original, expanded, bdd, limits, expansion)
        if candidate.proven:
            return Choice(candidate.expression, candidate.depends_on_expansion)
    return None


def _gather(
    candidates: list[_Candidate],
    original: Expression,
    expanded: Expression,
    bdd: BDD,
    limits: ResourceLimits,
    expansion: Expansion | None,
) -> None:
    """Add the other forms of the macro to ``candidates`` (the baseline is already there)."""
    if expansion is None:
        candidates.extend(_Candidate(form, False) for form in _other_orders(original, bdd, limits))
        return
    # The definition's own atoms, with the referenced macros left as names: a form found
    # here holds whatever those macros stand for.
    atoms = tuple(dict.fromkeys(expression_atoms_in_order(original)))
    own_bdd = BDD(atoms, limits=limits, budget=AnalysisBudget(min(limits.max_work, SEARCH_WORK)))
    own = exact_simplify(original, own_bdd)
    candidates.append(_Candidate(own, False, _equivalent(original, own, own_bdd)))
    candidates.extend(_Candidate(form, False) for form in _other_orders(original, own_bdd, limits))
    # Forms over the atoms the referenced macros expand to, then those forms recomposed.
    candidates.extend(_Candidate(form, True) for form in _other_orders(expanded, bdd, limits))
    named = _named_nodes(expansion.vocabulary(), bdd)
    if not named:
        return
    for form in [item.expression for item in candidates if item.depends_on_expansion]:
        folded = _fold(form, named, bdd)
        if expressions_differ(form, folded):
            candidates.append(_Candidate(folded, True))


def _prove(
    candidate: _Candidate,
    original: Expression,
    expanded: Expression,
    bdd: BDD,
    limits: ResourceLimits,
    expansion: Expansion | None,
) -> bool:
    """Whether ``candidate`` is equivalent to the macro, by a diagram that fits the limits."""
    if expansion is None:
        return _equivalent(original, candidate.expression, bdd)
    if not candidate.depends_on_expansion:
        atoms = tuple(dict.fromkeys(expression_atoms_in_order(original)))
        budget = AnalysisBudget(min(limits.max_work, SEARCH_WORK))
        return _equivalent(original, candidate.expression, BDD(atoms, limits=limits, budget=budget))
    try:
        form = expansion.expand(candidate.expression)
    except AnalysisLimitExceeded:
        return False
    return _equivalent(expanded, form, bdd)
