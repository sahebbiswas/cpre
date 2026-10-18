"""Integer values for simple ``NAME op INT`` comparisons in branch-covering configurations.

The Boolean model treats a comparison as one opaque fact. For the common shape of a macro
compared with an integer literal, a value can be chosen that makes every required
comparison on that macro hold together, so ``cover_branches`` need not leave such a
branch uncovered. Anything more complex stays opaque. Every value chosen here is only a
proposal: the configuration is still verified with concrete preprocessing.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .expressions import conjunction, disjunction, negate
from .model import BooleanAtom, DefinedVariable, Expression, Predicate, Variable

_NAME = r"[A-Za-z_]\w*"
_INTEGER = r"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)"
_NUMBER = rf"(?P<sign>-\s*)?(?P<value>{_INTEGER})"
_OPERATOR = r"(?P<op>==|!=|<=|>=|<|>)"
_NAME_FIRST = re.compile(rf"(?P<name>{_NAME})\s*{_OPERATOR}\s*{_NUMBER}\Z")
_NUMBER_FIRST = re.compile(rf"{_NUMBER}\s*{_OPERATOR}\s*(?P<name>{_NAME})\Z")

_OPERATORS: dict[str, Callable[[int, int], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_FLIPPED = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
_SIGNED_MIN = -(1 << 63)
_SIGNED_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class Comparison:
    """A required ``name op bound`` fact, as a Boolean atom's value demands it."""

    name: str
    op: str
    bound: int
    holds: bool

    def accepts(self, value: int) -> bool:
        return _OPERATORS[self.op](value, self.bound) == self.holds


def parse_comparison(text: str) -> tuple[str, str, int] | None:
    """Parse ``NAME op INT`` or ``INT op NAME`` (decimal or hex, optional minus); else None."""
    match = _NAME_FIRST.match(text)
    flipped = False
    if match is None:
        match = _NUMBER_FIRST.match(text)
        flipped = True
    if match is None:
        return None
    bound = int(match["value"], 0)
    if match["sign"]:
        bound = -bound
    if not _SIGNED_MIN <= bound <= _SIGNED_MAX:
        return None
    op = _FLIPPED[match["op"]] if flipped else match["op"]
    return match["name"], op, bound


def solve_integers(
    required: Mapping[BooleanAtom, bool], excluded: set[str]
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Choose one integer per macro that satisfies every required comparison on it.

    Returns the chosen values and, for macros whose requirements cannot hold together,
    their comparisons as text. A macro that must be undefined, or is in ``excluded``
    (fixed by the context), is left alone. Candidates are ``0``, ``1`` and each bound
    with its neighbours, tried in ascending order, so the result is deterministic.
    """
    constraints: dict[str, list[Comparison]] = {}
    spellings: dict[str, list[str]] = {}
    for atom, holds in required.items():
        if not isinstance(atom, Predicate):
            continue
        parsed = parse_comparison(atom.text)
        if parsed is None or parsed[0] in excluded:
            continue
        name, op, bound = parsed
        constraints.setdefault(name, []).append(Comparison(name, op, bound, holds))
        spellings.setdefault(name, []).append(atom.text if holds else f"!({atom.text})")

    values: dict[str, int] = {}
    contradictory: dict[str, list[str]] = {}
    for name in sorted(constraints):
        if required.get(DefinedVariable(name)) is False:
            continue
        truth = required.get(Variable(name))
        candidates = {0, 1}
        for comparison in constraints[name]:
            candidates.update((comparison.bound - 1, comparison.bound, comparison.bound + 1))
        for value in sorted(candidates):
            if not _SIGNED_MIN <= value <= _SIGNED_MAX:
                continue
            if truth is not None and (value != 0) != truth:
                continue
            if all(comparison.accepts(value) for comparison in constraints[name]):
                values[name] = value
                break
        else:
            contradictory[name] = sorted(set(spellings[name]))
    return values, contradictory


_MAX_COMPARISONS_PER_NAME = 8


def comparison_atoms(atoms: list[BooleanAtom]) -> dict[str, list[Predicate]]:
    """The ``NAME op INT`` predicates among ``atoms``, grouped by macro name."""
    grouped: dict[str, list[Predicate]] = {}
    for atom in atoms:
        if isinstance(atom, Predicate) and (parsed := parse_comparison(atom.text)) is not None:
            grouped.setdefault(parsed[0], []).append(atom)
    return grouped


def opaque_comparison_names(atoms: list[BooleanAtom]) -> set[str]:
    """Macros with more comparisons than the integer reasoning is willing to enumerate."""
    return {
        name
        for name, predicates in comparison_atoms(atoms).items()
        if len(predicates) > _MAX_COMPARISONS_PER_NAME
    }


def integer_semantics(atoms: list[BooleanAtom], excluded: set[str]) -> Expression | None:
    """Constraints that make the comparisons on one macro agree with some integer.

    For each macro, the truth values its comparisons can take together are exactly those
    of the candidate values (every region of the number line holds one of them), plus the
    undefined macro, which reads as ``0``. The result forbids every other combination, so
    ``V >= 5`` true with ``V >= 3`` false cannot be proposed. ``None`` when nothing applies.
    """
    over_budget = opaque_comparison_names(atoms)
    clauses: list[Expression] = []
    for name, predicates in sorted(comparison_atoms(atoms).items()):
        if name in excluded or name in over_budget:
            continue
        comparisons: list[tuple[Predicate, str, int]] = []
        for atom in predicates:
            parsed = parse_comparison(atom.text)
            assert parsed is not None
            comparisons.append((atom, parsed[1], parsed[2]))
        candidates = {0, 1}
        for _, _, bound in comparisons:
            candidates.update((bound - 1, bound, bound + 1))
        values = sorted(value for value in candidates if _SIGNED_MIN <= value <= _SIGNED_MAX)
        vectors = [_vector(name, comparisons, value, True) for value in values]
        vectors.append(_vector(name, comparisons, 0, False))
        clauses.append(disjunction(*vectors))
    return conjunction(*clauses) if clauses else None


def _vector(
    name: str, comparisons: list[tuple[Predicate, str, int]], value: int, defined: bool
) -> Expression:
    """The truth values of one macro's atoms when it has ``value`` (or is undefined)."""
    literals = [_literal(atom, _OPERATORS[op](value, bound)) for atom, op, bound in comparisons]
    literals.append(_literal(DefinedVariable(name), defined))
    literals.append(_literal(Variable(name), value != 0))
    return conjunction(*literals)


def _literal(atom: BooleanAtom, value: bool) -> Expression:
    return atom if value else negate(atom)
