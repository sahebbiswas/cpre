"""Types shared by evaluation and its trace, below the evaluator in the import order."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvaluationState(str, Enum):
    """Whether an evaluation produced a value."""

    CONCRETE = "concrete"
    SYMBOLIC = "symbolic"
    UNEVALUABLE = "unevaluable"


class TraceKind(str, Enum):
    """What a trace step records. The values are stable identifiers."""

    START = "start"
    EXPAND = "expand"
    EXPANDED = "expanded"
    WARNING = "warning"
    CONVERSION = "conversion"
    EVALUATE = "evaluate"
    RESULT = "result"
    STOP = "stop"


@dataclass(frozen=True)
class TraceStep:
    """One step of the derivation of an evaluation.

    ``text`` is what the step is about: the expression, the macro invocation or the
    subexpression. ``replacement`` is what an invocation expanded to. ``state``, ``value``
    and ``unsigned`` describe the outcome of a step that has one (``evaluate``, ``result``
    and ``stop``); ``note`` explains a warning, a conversion or where evaluation stopped.
    """

    kind: TraceKind
    text: str
    replacement: str | None = None
    state: EvaluationState | None = None
    value: int | None = None
    unsigned: bool | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "replacement": self.replacement,
            "state": None if self.state is None else self.state.value,
            "value": self.value,
            "unsigned": self.unsigned,
            "note": self.note,
        }


@dataclass(frozen=True)
class Trace:
    """The steps of an evaluation in order; ``truncated`` when the step limit was reached."""

    steps: tuple[TraceStep, ...] = ()
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"truncated": self.truncated, "steps": [step.to_dict() for step in self.steps]}


TRACE_LIMIT = 200


class TraceRecorder:
    """Collects steps up to a fixed limit, then only counts that some were dropped.

    A pass that is thrown away (the strict pass before the symbolic one) can be
    :meth:`rewind` to the steps recorded before it.
    """

    def __init__(self, limit: int = TRACE_LIMIT) -> None:
        self.limit = limit
        self.steps: list[TraceStep] = []
        self.truncated = False
        self._mark = 0

    def add(self, step: TraceStep, *, force: bool = False) -> None:
        if len(self.steps) >= self.limit and not force:
            self.truncated = True
            return
        self.steps.append(step)

    def mark(self) -> None:
        self._mark = len(self.steps)

    def rewind(self) -> None:
        del self.steps[self._mark :]
        self.truncated = False

    def trace(self) -> Trace:
        return Trace(tuple(self.steps), self.truncated)
