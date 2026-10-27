"""Evaluate an integer expression, or a macro that expands to one, to its value.

The evaluator is the one behind concrete ``#if`` conditions
(:mod:`cpre.numeric_conditions`), so results follow the preprocessor's controlling
expression rules: ``intmax_t``/``uintmax_t`` arithmetic, C division and remainder, and
no types beyond integers. A value that cannot be proved is an ``unevaluable`` result
with a reason, never a guess and never an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ._types import AnalysisOptions
from .configuration import MacroConfiguration, _condition_environment, _configured_environment
from .errors import AnalysisError, ErrorCode
from .expansion import Expansion, ExpansionError
from .macros import MacroLookup
from .numeric_conditions import FailureKind, IntegerEvaluation, evaluate_integer_expression
from .preprocessing import (
    _PREDEFINED_MACROS,
    PreprocessingContext,
    _configured_preprocessing_context,
)
from .robdd import AnalysisBudget, AnalysisLimitExceeded

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


class EvaluationState(str, Enum):
    """Whether an evaluation produced a value."""

    CONCRETE = "concrete"
    UNEVALUABLE = "unevaluable"


class EvaluationReason(str, Enum):
    """Why an evaluation has no value. The values are stable identifiers."""

    DIVISION_BY_ZERO = "division_by_zero"
    OVERFLOW = "overflow"
    UNRESOLVED_IDENTIFIER = "unresolved_identifier"
    UNSUPPORTED_SYNTAX = "unsupported_syntax"
    IMPLEMENTATION_DEFINED = "implementation_defined"
    FUNCTION_LIKE = "function_like"
    NESTING_BOUND = "nesting_bound"
    EXPANSION_ERROR = "expansion_error"
    BUDGET_EXCEEDED = "budget_exceeded"


_REASONS = {
    FailureKind.DIVISION_BY_ZERO: EvaluationReason.DIVISION_BY_ZERO,
    FailureKind.OVERFLOW: EvaluationReason.OVERFLOW,
    FailureKind.UNRESOLVED_IDENTIFIER: EvaluationReason.UNRESOLVED_IDENTIFIER,
    FailureKind.UNSUPPORTED: EvaluationReason.UNSUPPORTED_SYNTAX,
    FailureKind.IMPLEMENTATION_DEFINED: EvaluationReason.IMPLEMENTATION_DEFINED,
    FailureKind.NESTING_BOUND: EvaluationReason.NESTING_BOUND,
}


@dataclass(frozen=True)
class Evaluation:
    """The outcome of evaluating one expression or macro.

    ``kind`` is ``"expression"`` or ``"macro"`` and ``expression`` is the text that was
    evaluated (the macro's name for a macro). A ``concrete`` result has ``value`` and
    ``unsigned`` (whether it is a ``uintmax_t``). An ``unevaluable`` one has ``reason``
    and ``message`` instead, and ``offset``, the offset in ``expression`` of the token it
    concerns when one is known; a token that came from a macro's replacement is located
    at the macro that produced it. ``expanded`` is the expression after macro expansion,
    whenever expansion got that far.
    """

    kind: str
    expression: str
    state: EvaluationState
    value: int | None = None
    unsigned: bool | None = None
    expanded: str | None = None
    reason: EvaluationReason | None = None
    message: str | None = None
    offset: int | None = None

    @property
    def concrete(self) -> bool:
        return self.state is EvaluationState.CONCRETE

    def to_dict(self) -> dict[str, object]:
        """The JSON-ready record documented in the JSON contract (``cpre.evaluation/1``)."""
        return {
            "kind": self.kind,
            "expression": self.expression,
            "state": self.state.value,
            "value": self.value,
            "unsigned": self.unsigned,
            "expanded": self.expanded,
            "reason": None if self.reason is None else self.reason.value,
            "message": self.message,
            "offset": self.offset,
        }


def evaluate_expression(
    text: str,
    configuration: MacroConfiguration | None = None,
    *,
    context: PreprocessingContext | None = None,
    options: AnalysisOptions | None = None,
) -> Evaluation:
    """Evaluate integer expression ``text`` under ``configuration``.

    Macros in ``text`` are expanded first, exactly as in an ``#if`` controlling
    expression, including function-like calls and the ``unknown_names`` policy of the
    configuration (names it marks, or defaults to, undefined evaluate as ``0``).
    ``context`` supplies standard predefined macro values and ``options`` the work
    limit. Invalid arguments raise :class:`AnalysisError`; anything that merely prevents
    a value is reported in the returned :class:`Evaluation`.
    """
    _check_text(text, "expression")
    view, budget = _prepare(configuration, context, options)
    return _evaluate("expression", text, view, budget)


def evaluate_macro(
    name: str,
    configuration: MacroConfiguration,
    *,
    context: PreprocessingContext | None = None,
    options: AnalysisOptions | None = None,
) -> Evaluation:
    """Evaluate the object-like macro ``name`` under ``configuration``.

    The result is the value of ``name`` expanded on its own. Where the name is used
    inside a larger expression, an unparenthesized replacement such as ``A + B`` takes
    the precedence of its surroundings, so evaluate that use with
    :func:`evaluate_expression` instead. A macro that is function-like, not defined, or
    not settled by the configuration is unevaluable.
    """
    _check_text(name, "macro name")
    if _IDENTIFIER.match(name) is None:
        raise AnalysisError(
            f"macro name must be an identifier: {name!r}", code=ErrorCode.INVALID_CONFIGURATION
        )
    environment = _configured_environment(configuration)
    _configured_preprocessing_context(environment, context)
    budget = AnalysisBudget(_limits(options).max_work)
    state = environment.get(name)
    if state.definition is not None and state.definition.parameters is not None:
        return _unevaluable(
            "macro",
            name,
            EvaluationReason.FUNCTION_LIKE,
            f"{name} is a function-like macro; evaluate a call of it as an expression",
        )
    if state.definition is None:
        detail = "not defined" if state.defined is False else "not settled by the configuration"
        return _unevaluable(
            "macro", name, EvaluationReason.UNRESOLVED_IDENTIFIER, f"macro {name!r} is {detail}"
        )
    return _evaluate("macro", name, _condition_environment(environment), budget)


def _check_text(text: object, what: str) -> None:
    if not isinstance(text, str):
        raise AnalysisError(f"{what} must be a string", code=ErrorCode.INVALID_CONFIGURATION)


def _limits(options: AnalysisOptions | None) -> AnalysisOptions:
    resolved = options if options is not None else AnalysisOptions()
    if not isinstance(resolved, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance", code=ErrorCode.ANALYSIS_FAILURE
        )
    return resolved


def _prepare(
    configuration: MacroConfiguration | None,
    context: PreprocessingContext | None,
    options: AnalysisOptions | None,
) -> tuple[MacroLookup, AnalysisBudget]:
    environment = _configured_environment(
        configuration if configuration is not None else MacroConfiguration()
    )
    _configured_preprocessing_context(environment, context)
    return _condition_environment(environment), AnalysisBudget(_limits(options).max_work)


def _unevaluable(
    kind: str,
    expression: str,
    reason: EvaluationReason,
    message: str,
    *,
    expanded: str | None = None,
    offset: int | None = None,
) -> Evaluation:
    return Evaluation(
        kind,
        expression,
        EvaluationState.UNEVALUABLE,
        expanded=expanded,
        reason=reason,
        message=message,
        offset=offset,
    )


def _evaluate(kind: str, text: str, view: MacroLookup, budget: AnalysisBudget) -> Evaluation:
    expansion = Expansion(text, budget)
    expansion.current_offset = -1
    try:
        outcome = evaluate_integer_expression(
            text, view, expansion, budget, unsupported_identifiers=_PREDEFINED_MACROS
        )
    except ExpansionError as error:
        offset = expansion.current_offset
        return _unevaluable(
            kind,
            text,
            EvaluationReason.EXPANSION_ERROR,
            str(error),
            offset=offset if offset >= 0 else None,
        )
    except AnalysisLimitExceeded as error:
        return _unevaluable(kind, text, EvaluationReason.BUDGET_EXCEEDED, str(error))
    return _from_integer(kind, text, outcome)


def _from_integer(kind: str, text: str, outcome: IntegerEvaluation) -> Evaluation:
    if outcome.value is not None:
        return Evaluation(
            kind,
            text,
            EvaluationState.CONCRETE,
            value=outcome.value,
            unsigned=outcome.unsigned,
            expanded=outcome.expanded,
        )
    assert outcome.failure is not None
    return _unevaluable(
        kind,
        text,
        _REASONS[outcome.failure],
        outcome.message or "expression has no concrete value",
        expanded=outcome.expanded,
        offset=outcome.offset,
    )


__all__ = [
    "Evaluation",
    "EvaluationReason",
    "EvaluationState",
    "evaluate_expression",
    "evaluate_macro",
]
