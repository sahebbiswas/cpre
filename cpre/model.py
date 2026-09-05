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
class DefinedVariable:
    """Whether a preprocessor macro is defined, distinct from its Boolean value."""

    name: str


@dataclass(frozen=True)
class Predicate:
    """A value-bearing expression treated as one opaque Boolean fact."""

    text: str


@dataclass(frozen=True)
class Negation:
    operand: "Expression"


@dataclass(frozen=True)
class Conjunction:
    operands: tuple["Expression", ...]


@dataclass(frozen=True)
class Disjunction:
    operands: tuple["Expression", ...]


BooleanAtom = typing.Union[Variable, DefinedVariable, Predicate]
Expression = typing.Union[
    Constant, Variable, DefinedVariable, Predicate, Negation, Conjunction, Disjunction
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
    children: list["ConditionalGroup"] = field(default_factory=list)
    analysis: "BranchAnalysis | None" = None


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
    "TRUE",
    "Variable",
]
