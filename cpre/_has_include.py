"""Shared ``__has_include`` scanning and query construction for every preprocessed source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, tokenize
from .includes import IncludeForm
from .macros import MacroDefinition, MacroEnvironment, MacroState
from .parser import DIRECTIVE_RE, logical_lines
from .robdd import AnalysisBudget


@dataclass(frozen=True)
class IncludeQuery:
    """One caller-resolved standard header-availability query.

    ``header`` contains the spelling between delimiters after macro expansion.
    ``location`` is the physical location of ``__has_include`` in the source that
    contains it. ``filename`` is the ``filename`` supplied to
    :func:`preprocess_source` for the primary source, or the resolved identity of
    the included source that contains the query.
    """

    header: str
    form: IncludeForm
    location: SourceLocation
    filename: str | None = None


class IncludeQueryProvider(Protocol):
    """Callable host interface for deterministic include availability."""

    def __call__(self, query: IncludeQuery) -> bool | None:
        """Return availability, or ``None`` when the host cannot answer."""
        ...


HAS_INCLUDE = "__has_include"
HAS_INCLUDE_NEXT = "__has_include_next"


@dataclass(frozen=True)
class Occurrence:
    """One ``__has_include`` invocation in a reachable-candidate condition."""

    placeholder: str
    operator: str
    operand: str | None
    text: str
    start: int
    end: int
    location: SourceLocation
    directive_line: int


class HeaderOperandError(ValueError):
    pass


def _physical_line_starts(source: str) -> list[int]:
    starts = [0]
    offset = 0
    for line in source.splitlines(keepends=True):
        offset += len(line)
        starts.append(offset)
    return starts


def _offset(starts: list[int], location: SourceLocation) -> int:
    assert location.column is not None
    return starts[location.line - 1] + location.column - 1


def _public_location(location: object) -> SourceLocation:
    """Convert the parser's internal location shape to the stable public type."""
    line = getattr(location, "line", None)
    column = getattr(location, "column", None)
    if line is None:
        raise AnalysisError(
            "conditional source location is missing a physical line",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    return SourceLocation(line, column)


def _placeholder(index: int, source: str, used: set[str]) -> str:
    suffix = 0
    while True:
        tail = f"{index:x}" if suffix == 0 else f"{index:x}_{suffix:x}"
        candidate = f"_HI{tail}"
        if candidate not in source and candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def _is_defined_operand(parts: list, index: int) -> bool:
    """Whether ``parts[index]`` is the operand of ``defined`` or ``defined (``."""
    previous = [part.text for part in parts[:index] if part.kind not in {"space", "comment"}][-2:]
    return previous[-1:] == ["defined"] or previous == ["defined", "("]


def scan(source: str) -> tuple[Occurrence, ...]:
    """Find ``__has_include``/``__has_include_next`` invocations in ``#if``/``#elif`` lines.

    Operands of ``defined`` are feature tests of the operator itself and are left
    for ordinary definedness evaluation.
    """
    starts = _physical_line_starts(source)
    found: list[Occurrence] = []
    used: set[str] = set()

    for logical in logical_lines(source):
        match = DIRECTIVE_RE.match(logical.text)
        if match is None or match.group(1) not in {"if", "elif"}:
            continue
        expression = match.group(2)
        expression_start = match.start(2)
        expression_locations = logical.locations[
            expression_start : expression_start + len(expression)
        ]
        parts = tokenize(expression)
        index = 0
        while index < len(parts):
            token = parts[index]
            if (
                token.kind != "identifier"
                or token.text not in {HAS_INCLUDE, HAS_INCLUDE_NEXT}
                or _is_defined_operand(parts, index)
            ):
                index += 1
                continue

            cursor = index + 1
            while cursor < len(parts) and parts[cursor].kind in {"space", "comment"}:
                cursor += 1

            operand: str | None = None
            invocation_end = token.end
            next_index = index + 1
            if cursor < len(parts) and parts[cursor].text == "(":
                opening = parts[cursor]
                depth = 0
                closing = None
                probe = cursor + 1
                while probe < len(parts):
                    current = parts[probe]
                    if current.text == "(":
                        depth += 1
                    elif current.text == ")":
                        if depth == 0:
                            closing = current
                            break
                        depth -= 1
                    probe += 1
                if closing is not None:
                    operand = expression[opening.end : closing.start]
                    invocation_end = closing.end
                    next_index = probe + 1
                else:
                    invocation_end = len(expression)
                    next_index = len(parts)

            start_location = _public_location(expression_locations[token.start])
            end_location = _public_location(expression_locations[invocation_end - 1])
            found.append(
                Occurrence(
                    _placeholder(len(found), source, used),
                    token.text,
                    operand,
                    expression[token.start : invocation_end],
                    _offset(starts, start_location),
                    _offset(starts, end_location) + 1,
                    start_location,
                    logical.start_line,
                )
            )
            index = next_index

    return tuple(found)


def render(source: str, occurrences: tuple[Occurrence, ...]) -> str:
    """Replace each invocation with its placeholder identifier, preserving layout."""
    characters = list(source)
    for occurrence in occurrences:
        writable: list[int] = []
        position = occurrence.start
        while position < occurrence.end:
            char = source[position]
            if char in "\r\n":
                position += 1
                continue
            if char == "\\" and position + 1 < occurrence.end and source[position + 1] in "\r\n":
                position += 1
                continue
            writable.append(position)
            position += 1
        if len(occurrence.placeholder) > len(writable):
            raise AnalysisError(
                "internal __has_include placeholder exceeds invocation width",
                code=ErrorCode.ANALYSIS_FAILURE,
                location=occurrence.location,
            )
        for position in writable:
            characters[position] = " "
        for position, char in zip(writable, occurrence.placeholder):
            characters[position] = char
    return "".join(characters)


def restore(text: str, occurrences: list[Occurrence]) -> str:
    """Replace placeholders in condition text with the invocations as written."""
    written = {occurrence.placeholder: occurrence.text for occurrence in occurrences}
    if not written:
        return text
    pattern = r"\b(?:" + "|".join(written) + r")\b"
    return re.sub(pattern, lambda match: written[match[0]], text)


def header_query(
    occurrence: Occurrence,
    environment: MacroEnvironment,
    budget: AnalysisBudget,
    filename: str | None,
) -> IncludeQuery:
    """Macro-expand an invocation operand into a header-name query."""
    if occurrence.operand is None:
        raise HeaderOperandError("__has_include expects one parenthesized header-name operand")

    fragment = Expansion(occurrence.operand, budget)
    significant = [token for token in fragment.tokens if token.kind not in {"space", "comment"}]
    expanded = [
        token for token in fragment._expand(significant, environment) if token.kind != "empty"
    ]

    form: IncludeForm
    header: str
    if (
        len(expanded) == 1
        and expanded[0].kind == "literal"
        and re.fullmatch(r'"(?:\\.|[^"\\])*"', expanded[0].text, re.DOTALL)
    ):
        form = IncludeForm.QUOTED
        header = expanded[0].text[1:-1]
    elif len(expanded) >= 3 and expanded[0].text == "<" and expanded[-1].text == ">":
        if any(token.text == ">" for token in expanded[1:-1]):
            raise HeaderOperandError("malformed angle-bracket header name in __has_include")
        form = IncludeForm.ANGLE
        header = "".join(token.text for token in expanded[1:-1])
        if not header:
            raise HeaderOperandError("empty angle-bracket header name in __has_include")
    else:
        raise HeaderOperandError('__has_include operand must macro-expand to "header" or <header>')

    return IncludeQuery(header, form, occurrence.location, filename)


class ConditionView:
    """Condition-evaluation view of a macro environment with ``__has_include`` answers.

    Placeholders read as unknown until answered, then as ``1`` or ``0``.
    ``__has_include`` itself reads as defined when the caller can answer queries
    and as unknown otherwise, so ``defined(__has_include)`` never guesses;
    the unsupported ``__has_include_next`` reads as undefined.
    """

    def __init__(
        self,
        environment: MacroEnvironment,
        occurrences: tuple[Occurrence, ...],
        answerable: bool,
    ) -> None:
        self._environment = environment
        self._placeholders = frozenset(item.placeholder for item in occurrences)
        self._answerable = answerable
        self.answers: dict[str, bool] = {}

    def get(self, name: str) -> MacroState:
        if name in self._placeholders:
            answer = self.answers.get(name)
            if answer is None:
                return MacroState()
            return MacroState(True, answer, MacroDefinition(name, "1" if answer else "0"))
        if name == HAS_INCLUDE:
            return MacroState(True, None) if self._answerable else MacroState()
        if name == HAS_INCLUDE_NEXT:
            # Not implemented, so feature tests take their fallback path.
            return MacroState(False, False)
        return self._environment.get(name)
