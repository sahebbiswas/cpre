"""Stable exact Boolean proof/query facade for downstream consumers.

This module intentionally hides the internal ROBDD manager, node identifiers,
and atom-order tables. Public consumers should import these names from the
 top-level :mod:`cpre` package.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .api import AnalysisIncomplete, AnalysisOptions
from .errors import AnalysisError, ErrorCode
from .model import BooleanAtom, DefinedVariable, Expression, Predicate, TRUE, Variable
from .robdd import AnalysisBudget as _AnalysisBudget
from .robdd import AnalysisLimitExceeded as _AnalysisLimitExceeded
from .robdd import BDD as _BDD
from .robdd import exact_simplify as _exact_simplify
from .symbolic import normalize, ordered_atoms


class WitnessAtomKind(str, Enum):
    """Semantic category of one Boolean witness assignment."""

    MACRO_VALUE = "macro_value"
    MACRO_DEFINED = "macro_defined"
    PREDICATE = "predicate"


@dataclass(frozen=True)
class WitnessAssignment:
    """One deterministic Boolean assignment without exposing internal atoms."""

    kind: WitnessAtomKind
    symbol: str
    value: bool


@dataclass(frozen=True)
class SatisfiabilityResult:
    """Result of :func:`satisfiable`.

    ``satisfiable`` is ``None`` only when exact analysis could not complete.
    """

    satisfiable: bool | None
    incomplete: AnalysisIncomplete | None = None

    @property
    def complete(self) -> bool:
        return self.incomplete is None


@dataclass(frozen=True)
class ProofResult:
    """Result of an implication or equivalence proof.

    ``holds`` is ``None`` only when exact analysis could not complete.
    """

    holds: bool | None
    incomplete: AnalysisIncomplete | None = None

    @property
    def complete(self) -> bool:
        return self.incomplete is None


@dataclass(frozen=True)
class SimplificationResult:
    """Result of :func:`exact_simplify`.

    On incomplete analysis ``expression`` is ``None``; callers never receive an
    unproven rewrite.
    """

    expression: Expression | None
    incomplete: AnalysisIncomplete | None = None

    @property
    def complete(self) -> bool:
        return self.incomplete is None


@dataclass(frozen=True)
class WitnessResult:
    """Deterministic satisfying witness, UNSAT result, or incomplete result."""

    satisfiable: bool | None
    assignment: tuple[WitnessAssignment, ...] | None
    incomplete: AnalysisIncomplete | None = None

    @property
    def complete(self) -> bool:
        return self.incomplete is None


def _atom_key(atom: BooleanAtom) -> tuple[str, str]:
    if isinstance(atom, DefinedVariable):
        return ("defined", atom.name)
    if isinstance(atom, Variable):
        return ("variable", atom.name)
    return ("predicate", atom.text)


def _prepare(expressions: Iterable[Expression]) -> tuple[tuple[Expression, ...], tuple[BooleanAtom, ...]]:
    originals = tuple(expressions)
    atoms: set[BooleanAtom] = set()
    normalized: list[Expression] = []
    for expression in originals:
        # ordered_atoms validates the public expression shape while preserving
        # atoms that normalization may prove to be don't-cares.
        atoms.update(ordered_atoms(expression))
        normalized.append(normalize(expression))
    return tuple(normalized), tuple(sorted(atoms, key=_atom_key))


def _options(options: AnalysisOptions | None) -> AnalysisOptions:
    resolved = options or AnalysisOptions()
    if not isinstance(resolved, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    return resolved


def _bdd(atoms: tuple[BooleanAtom, ...], options: AnalysisOptions) -> _BDD:
    limits = options._resource_limits()
    return _BDD(atoms, limits=limits, budget=_AnalysisBudget(limits.max_work))


def _incomplete(error: _AnalysisLimitExceeded) -> AnalysisIncomplete:
    return AnalysisIncomplete(
        code=ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
        resource=error.resource,
        limit=error.limit,
        observed=error.observed,
        message=str(error),
    )


def satisfiable(
    expression: Expression,
    *,
    options: AnalysisOptions | None = None,
) -> SatisfiabilityResult:
    """Prove whether *expression* has at least one Boolean assignment."""

    (expression,), atoms = _prepare((expression,))
    try:
        value = _bdd(atoms, _options(options)).satisfiable(expression)
    except _AnalysisLimitExceeded as error:
        return SatisfiabilityResult(None, _incomplete(error))
    return SatisfiabilityResult(value)


def implies(
    premise: Expression,
    consequence: Expression,
    *,
    options: AnalysisOptions | None = None,
) -> ProofResult:
    """Prove whether every assignment satisfying *premise* satisfies *consequence*."""

    (premise, consequence), atoms = _prepare((premise, consequence))
    try:
        bdd = _bdd(atoms, _options(options))
        counterexample = bdd.apply(
            "and",
            bdd.build(premise),
            bdd.negate(bdd.build(consequence)),
        )
        holds = counterexample == 0
    except _AnalysisLimitExceeded as error:
        return ProofResult(None, _incomplete(error))
    return ProofResult(holds)


def equivalent(
    left: Expression,
    right: Expression,
    *,
    options: AnalysisOptions | None = None,
) -> ProofResult:
    """Prove whether *left* and *right* agree for every Boolean assignment."""

    (left, right), atoms = _prepare((left, right))
    try:
        bdd = _bdd(atoms, _options(options))
        holds = bdd.equivalent_under(TRUE, left, right)
    except _AnalysisLimitExceeded as error:
        return ProofResult(None, _incomplete(error))
    return ProofResult(holds)


def exact_simplify(
    expression: Expression,
    *,
    options: AnalysisOptions | None = None,
) -> SimplificationResult:
    """Return a proven globally equivalent simplification of *expression*.

    If a resource limit is reached, no expression is returned. The caller may
    keep the original expression or choose another conservative policy.
    """

    (expression,), atoms = _prepare((expression,))
    try:
        simplified = _exact_simplify(expression, _bdd(atoms, _options(options)))
    except _AnalysisLimitExceeded as error:
        return SimplificationResult(None, _incomplete(error))
    return SimplificationResult(normalize(simplified))


def _witness_values(bdd: _BDD, root: int) -> dict[BooleanAtom, bool]:
    # False-first traversal makes the satisfying choice deterministic. Every
    # atom starts false, which is the documented fill policy for don't-cares.
    values = {atom: False for atom in bdd.atoms}
    node = root
    while node >= 2:
        bdd.budget.consume()
        item = bdd.nodes[node]
        assert item is not None
        variable_index, low, high = item
        if low != 0:
            values[bdd.atoms[variable_index]] = False
            node = low
        else:
            values[bdd.atoms[variable_index]] = True
            node = high
    return values


def _public_assignment(atom: BooleanAtom, value: bool) -> WitnessAssignment:
    if isinstance(atom, DefinedVariable):
        return WitnessAssignment(WitnessAtomKind.MACRO_DEFINED, atom.name, value)
    if isinstance(atom, Variable):
        return WitnessAssignment(WitnessAtomKind.MACRO_VALUE, atom.name, value)
    assert isinstance(atom, Predicate)
    return WitnessAssignment(WitnessAtomKind.PREDICATE, atom.text, value)


def witness_assignment(
    expression: Expression,
    *,
    options: AnalysisOptions | None = None,
) -> WitnessResult:
    """Return a deterministic satisfying Boolean assignment for *expression*.

    Assignments are ordered by semantic atom category/name. False is used for
    don't-care atoms. Opaque predicates receive only a Boolean truth value; no
    integer value is invented. UNSAT is represented by ``satisfiable=False``
    with ``assignment=None``; incomplete analysis uses ``satisfiable=None``.
    """

    (expression,), atoms = _prepare((expression,))
    try:
        bdd = _bdd(atoms, _options(options))
        root = bdd.build(expression)
        if root == 0:
            return WitnessResult(False, None)
        values = _witness_values(bdd, root)
        assignment = tuple(_public_assignment(atom, values[atom]) for atom in bdd.atoms)
    except _AnalysisLimitExceeded as error:
        return WitnessResult(None, None, _incomplete(error))
    return WitnessResult(True, assignment)


__all__ = [
    "ProofResult",
    "SatisfiabilityResult",
    "SimplificationResult",
    "WitnessAssignment",
    "WitnessAtomKind",
    "WitnessResult",
    "equivalent",
    "exact_simplify",
    "implies",
    "satisfiable",
    "witness_assignment",
]
