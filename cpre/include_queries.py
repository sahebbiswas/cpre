"""Host-assisted support for the standard ``__has_include`` operator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from .api import AnalysisIncomplete, AnalysisOptions, MacroAssumptions
from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, ExpansionError, tokenize
from .macros import MacroEnvironment
from .parser import DIRECTIVE_RE, logical_lines
from .preprocessing import (
    PreprocessingContext,
    PreprocessDiagnostic,
    PreprocessResult,
    preprocess_source as _core_preprocess_source,
)
from .robdd import AnalysisBudget, AnalysisLimitExceeded


class IncludeForm(str, Enum):
    """Syntactic form used by a ``__has_include`` query."""

    QUOTED = "quoted"
    ANGLE = "angle"


@dataclass(frozen=True)
class IncludeQuery:
    """One caller-resolved standard header-availability query.

    ``header`` contains the spelling between delimiters after macro expansion.
    ``location`` is the physical source location of ``__has_include`` and
    ``filename`` is the source identity supplied to :func:`preprocess_source`.
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


@dataclass(frozen=True)
class _Occurrence:
    index: int
    placeholder: str
    operand: str | None
    start: int
    end: int
    location: SourceLocation

    @property
    def line(self) -> int:
        return self.location.line


class _HeaderOperandError(ValueError):
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


def _placeholder(index: int, source: str, blocked: set[str]) -> str:
    suffix = 0
    while True:
        tail = f"{index:x}" if suffix == 0 else f"{index:x}_{suffix:x}"
        candidate = f"_HI{tail}"
        if candidate not in source and candidate not in blocked:
            blocked.add(candidate)
            return candidate
        suffix += 1


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


def _scan_occurrences(
    source: str,
    blocked_names: set[str],
) -> tuple[_Occurrence, ...]:
    starts = _physical_line_starts(source)
    found: list[_Occurrence] = []

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
            if token.kind != "identifier" or token.text != "__has_include":
                index += 1
                continue

            invocation_start = token.start
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

            start_location = _public_location(expression_locations[invocation_start])
            end_location = _public_location(expression_locations[invocation_end - 1])
            start_offset = _offset(starts, start_location)
            end_offset = _offset(starts, end_location) + 1
            placeholder = _placeholder(len(found), source, blocked_names)
            found.append(_Occurrence(
                len(found),
                placeholder,
                operand,
                start_offset,
                end_offset,
                start_location,
            ))
            index = next_index

    return tuple(found)


def _render_source(
    source: str,
    occurrences: tuple[_Occurrence, ...],
    resolved: Mapping[int, bool],
) -> str:
    characters = list(source)
    for occurrence in occurrences:
        replacement = (
            "1" if resolved.get(occurrence.index) is True
            else "0" if resolved.get(occurrence.index) is False
            else occurrence.placeholder
        )
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
        if len(replacement) > len(writable):
            raise AnalysisError(
                "internal __has_include placeholder exceeds invocation width",
                code=ErrorCode.ANALYSIS_FAILURE,
                location=occurrence.location,
            )
        for position in writable:
            characters[position] = " "
        for position, char in zip(writable, replacement):
            characters[position] = char
    return "".join(characters)


def _conditional_depth(source: str) -> int:
    depth = 0
    for logical in logical_lines(source):
        match = DIRECTIVE_RE.match(logical.text)
        if match is None:
            continue
        kind = match.group(1)
        if kind in {"if", "ifdef", "ifndef"}:
            depth += 1
        elif kind == "endif":
            depth = max(0, depth - 1)
    return depth


def _environment_before_line(
    source: str,
    line: int,
    *,
    filename: str | None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None,
    configuration: MacroConfiguration | None,
    context: PreprocessingContext | None,
    options: AnalysisOptions | None,
) -> MacroEnvironment | None:
    physical = source.splitlines(keepends=True)
    prefix = "".join(physical[: line - 1])
    depth = _conditional_depth(prefix)
    if depth:
        prefix += "".join("#endif\n" for _ in range(depth))
    result = _core_preprocess_source(
        prefix,
        filename=filename,
        assumptions=assumptions,
        configuration=configuration,
        context=context,
        options=options,
    )
    if not result.complete or result.macros is None:
        return None

    environment = MacroEnvironment()
    for name, state in result.macros.items():
        if state.definition is not None:
            environment.define(state.definition)
        elif state.defined is False:
            environment.undef(name)
    return environment


def _expanded_header(
    occurrence: _Occurrence,
    environment: MacroEnvironment,
    options: AnalysisOptions | None,
    filename: str | None,
) -> IncludeQuery:
    if occurrence.operand is None:
        raise _HeaderOperandError("__has_include expects one parenthesized header-name operand")

    resolved_options = options if options is not None else AnalysisOptions()
    fragment = Expansion(
        occurrence.operand,
        AnalysisBudget(resolved_options._resource_limits().max_work),
    )
    significant = [
        token
        for token in fragment.tokens
        if token.kind not in {"space", "comment"}
    ]
    expanded = [
        token
        for token in fragment._expand(significant, environment)
        if token.kind != "empty"
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
            raise _HeaderOperandError("malformed angle-bracket header name in __has_include")
        form = IncludeForm.ANGLE
        header = "".join(token.text for token in expanded[1:-1])
        if not header:
            raise _HeaderOperandError("empty angle-bracket header name in __has_include")
    else:
        raise _HeaderOperandError(
            "__has_include operand must macro-expand to \"header\" or <header>"
        )

    return IncludeQuery(header, form, occurrence.location, filename)


def _diagnostic_result(
    filename: str | None,
    diagnostic: PreprocessDiagnostic | AnalysisIncomplete,
) -> PreprocessResult:
    return PreprocessResult(None, filename, (diagnostic,))


def _blocked_names(
    assumptions: MacroAssumptions | Mapping[str, bool] | None,
    configuration: MacroConfiguration | None,
) -> set[str]:
    blocked: set[str] = set()
    if isinstance(assumptions, MacroAssumptions):
        blocked.update(assumptions.defined)
        blocked.update(assumptions.undefined)
        blocked.update(name for name, _ in assumptions.values)
    elif isinstance(assumptions, Mapping):
        blocked.update(str(name) for name in assumptions)
    if isinstance(configuration, MacroConfiguration):
        blocked.update(configuration.undefined)
        blocked.update(definition.name for definition in configuration.definitions)
    return blocked


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    configuration: MacroConfiguration | None = None,
    context: PreprocessingContext | None = None,
    include_query: IncludeQueryProvider | None = None,
    options: AnalysisOptions | None = None,
) -> PreprocessResult:
    """Concrete preprocessing with optional host-assisted ``__has_include`` support.

    cpre never searches the filesystem or emulates compiler include paths. When a
    reachable condition requires ``__has_include``, ``include_query`` receives the
    macro-expanded header spelling, delimiter form, physical source location, and
    filename. A Boolean answer is substituted deterministically; ``None`` yields an
    atomic incomplete result. Queries in branches or Boolean terms proven unreachable
    are never sent to the provider.
    """
    if include_query is not None and not callable(include_query):
        raise AnalysisError(
            "include_query must be callable",
            code=ErrorCode.INVALID_CONFIGURATION,
        )

    occurrences = _scan_occurrences(source, _blocked_names(assumptions, configuration))
    if not occurrences:
        return _core_preprocess_source(
            source,
            filename=filename,
            assumptions=assumptions,
            configuration=configuration,
            context=context,
            options=options,
        )

    resolved: dict[int, bool] = {}
    while True:
        transformed = _render_source(source, occurrences, resolved)
        result = _core_preprocess_source(
            transformed,
            filename=filename,
            assumptions=assumptions,
            configuration=configuration,
            context=context,
            options=options,
        )
        if result.complete:
            return result

        trigger_line = None
        for diagnostic in result.incomplete:
            if diagnostic.location is None:
                continue
            if diagnostic.code not in {
                ErrorCode.UNRESOLVED_CONDITION,
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
            }:
                continue
            if any(
                occurrence.index not in resolved
                and occurrence.line == diagnostic.location.line
                for occurrence in occurrences
            ):
                trigger_line = diagnostic.location.line
                break
        if trigger_line is None:
            return result

        occurrence = next(
            item
            for item in occurrences
            if item.index not in resolved and item.line == trigger_line
        )
        if occurrence.operand is None:
            return _diagnostic_result(
                filename,
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                    "__has_include expects one parenthesized header-name operand",
                    occurrence.location,
                ),
            )
        if include_query is None:
            return _diagnostic_result(
                filename,
                PreprocessDiagnostic(
                    ErrorCode.UNRESOLVED_CONDITION,
                    "__has_include requires caller-provided include availability",
                    occurrence.location,
                ),
            )

        environment = _environment_before_line(
            transformed,
            trigger_line,
            filename=filename,
            assumptions=assumptions,
            configuration=configuration,
            context=context,
            options=options,
        )
        if environment is None:
            return result

        try:
            query = _expanded_header(occurrence, environment, options, filename)
        except _HeaderOperandError as error:
            return _diagnostic_result(
                filename,
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                    str(error),
                    occurrence.location,
                ),
            )
        except ExpansionError as error:
            return _diagnostic_result(
                filename,
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                    str(error),
                    occurrence.location,
                ),
            )
        except AnalysisLimitExceeded as error:
            return _diagnostic_result(
                filename,
                AnalysisIncomplete(
                    ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
                    error.resource,
                    error.limit,
                    error.observed,
                    str(error),
                    occurrence.location,
                ),
            )

        available = include_query(query)
        if available is None:
            delimiter = (
                f'"{query.header}"'
                if query.form is IncludeForm.QUOTED
                else f"<{query.header}>"
            )
            return _diagnostic_result(
                filename,
                PreprocessDiagnostic(
                    ErrorCode.UNRESOLVED_CONDITION,
                    f"include availability is unknown for {delimiter}",
                    occurrence.location,
                ),
            )
        if type(available) is not bool:
            raise AnalysisError(
                "include_query must return True, False, or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=occurrence.location,
            )
        resolved[occurrence.index] = available


# Importing the package initializes this module after ``cpre.preprocessing``.
# Keep the documented submodule path compatible with the host-assisted wrapper.
from . import preprocessing as _preprocessing_module

_preprocessing_module.preprocess_source = preprocess_source


__all__ = [
    "IncludeForm",
    "IncludeQuery",
    "IncludeQueryProvider",
    "preprocess_source",
]
