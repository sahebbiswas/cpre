"""Internal analyzer data model.

This module is not part of the top-level public API. Public consumers should
import supported symbols from :mod:`cpre`.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Constant:
    value: bool


@dataclass(frozen=True)
class Variable:
    name: str


@dataclass(frozen=True)
class DefinedVariable(Variable):
    """Whether a preprocessor macro is defined, distinct from its Boolean value."""


@dataclass(frozen=True)
class SymbolicLiteral(Variable):
    """An integer literal deliberately treated as a free Boolean atom.

    Only produced by opt-in symbolic-literal macro analysis. ``name`` holds the
    canonical decimal spelling of the literal (for example ``"0"``), which can
    never collide with a C identifier.
    """


@dataclass(frozen=True)
class Predicate:
    """A value-bearing expression treated as one opaque Boolean fact."""

    text: str


@dataclass(frozen=True)
class Negation:
    operand: Expression

    def __hash__(self) -> int:
        # Computed once: hashing walks the whole tree, and the algebra hashes the same
        # subtrees over and over.
        value: int | None = getattr(self, "_hash", None)
        if value is None:
            value = hash(("Negation", self.operand))
            try:
                object.__setattr__(self, "_hash", value)
            except AttributeError:  # no instance dictionary (slots): just do not remember it
                pass
        return value


@dataclass(frozen=True)
class Conjunction:
    operands: tuple[Expression, ...]

    def __hash__(self) -> int:
        # Computed once: hashing walks the whole tree, and the algebra hashes the same
        # subtrees over and over.
        value: int | None = getattr(self, "_hash", None)
        if value is None:
            value = hash(("Conjunction", self.operands))
            try:
                object.__setattr__(self, "_hash", value)
            except AttributeError:  # no instance dictionary (slots): just do not remember it
                pass
        return value


@dataclass(frozen=True)
class Disjunction:
    operands: tuple[Expression, ...]

    def __hash__(self) -> int:
        # Computed once: hashing walks the whole tree, and the algebra hashes the same
        # subtrees over and over.
        value: int | None = getattr(self, "_hash", None)
        if value is None:
            value = hash(("Disjunction", self.operands))
            try:
                object.__setattr__(self, "_hash", value)
            except AttributeError:  # no instance dictionary (slots): just do not remember it
                pass
        return value


# The public aliases stay typing.Union (not ``X | Y``) so their repr and typing.get_args()
# results are unchanged for downstream code.
BooleanAtom = typing.Union[Variable, DefinedVariable, SymbolicLiteral, Predicate]  # noqa: UP007
Expression = typing.Union[  # noqa: UP007
    Constant,
    Variable,
    DefinedVariable,
    SymbolicLiteral,
    Predicate,
    Negation,
    Conjunction,
    Disjunction,
]
TRUE = Constant(True)
FALSE = Constant(False)


@dataclass(frozen=True)
class SourceLocation:
    line: int | None
    column: int


def format_location(location: SourceLocation) -> str:
    if location.line is None:
        return f"column {location.column}"
    return f"line {location.line}, column {location.column}"


class ConditionError(ValueError):
    """Base class for input errors reported by the analyzer."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        location: SourceLocation | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.location = location
        super().__init__(message)


class ExpressionSyntaxError(ConditionError):
    """Raised for a malformed Boolean expression."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "expression_syntax",
        location: SourceLocation | None = None,
    ) -> None:
        super().__init__(message, code=code, location=location)
        if location is not None:
            self.args = (f"{message} at {format_location(location)}",)


class DirectiveStructureError(ConditionError):
    """Raised for unmatched or misplaced conditional directives."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        location: SourceLocation,
    ) -> None:
        super().__init__(message, code=code, location=location)
        self.args = (f"line {location.line}: {message}",)


@dataclass
class ConditionalBranch:
    directive: str
    line: int
    expression_text: str | None
    expression: Expression | None
    children: list[ConditionalGroup] = field(default_factory=list)
    analysis: BranchAnalysis | None = None


@dataclass
class ConditionalGroup:
    line: int
    end_line: int | None = None
    branches: list[ConditionalBranch] = field(default_factory=list)


@dataclass
class ConditionalTree:
    groups: list[ConditionalGroup] = field(default_factory=list)


@dataclass(frozen=True)
class BranchAnalysis:
    status: str
    simplified: Expression | None
    contextual: Expression | None
    effective: Expression
    reason: str | None = None


__all__ = [
    "BooleanAtom",
    "BranchAnalysis",
    "ConditionError",
    "ConditionalBranch",
    "ConditionalGroup",
    "ConditionalTree",
    "Conjunction",
    "Constant",
    "DefinedVariable",
    "DirectiveStructureError",
    "Disjunction",
    "Expression",
    "ExpressionSyntaxError",
    "FALSE",
    "Negation",
    "Predicate",
    "SourceLocation",
    "SymbolicLiteral",
    "TRUE",
    "Variable",
]
