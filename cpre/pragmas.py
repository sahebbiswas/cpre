"""Host-owned handling for standard pragma syntax and ``_Pragma``."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Protocol

from .api import AnalysisOptions, MacroAssumptions
from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import SourceMapping, tokenize
from .include_queries import IncludeQueryProvider, preprocess_source as _include_preprocess_source
from .parser import logical_lines
from .preprocessing import PreprocessingContext, PreprocessDiagnostic, PreprocessResult


class PragmaOrigin(str, Enum):
    """Surface syntax that produced a host-visible pragma."""

    DIRECTIVE = "directive"
    OPERATOR = "operator"


class PragmaDisposition(str, Enum):
    """Host decision for a reachable pragma."""

    CONSUME = "consume"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Pragma:
    """One reachable pragma after standard syntax processing.

    ``payload`` is the preprocessing-token spelling after comments and line
    splices for ``#pragma``, or the standard destringized payload for
    ``_Pragma``. ``location`` always points back to the physical directive or
    operator invocation in the caller's source.
    """

    payload: str
    origin: PragmaOrigin
    location: SourceLocation
    filename: str | None = None


class PragmaHandler(Protocol):
    """Callable host policy for implementation-defined pragma meaning."""

    def __call__(self, pragma: Pragma) -> PragmaDisposition | None:
        """Consume a pragma, or return unsupported/None when it is unknown."""
        ...


@dataclass(frozen=True)
class _SourcePragma:
    payload: str
    location: SourceLocation
    marker_offset: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _RenderedPragma:
    pragma: Pragma
    output_start: int
    output_end: int
    source_lines: tuple[int, ...] = ()


class _MalformedPragma(ValueError):
    def __init__(self, message: str, location: SourceLocation) -> None:
        super().__init__(message)
        self.location = location


def _physical_line_starts(source: str) -> list[int]:
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _source_location(source: str, offset: int) -> SourceLocation:
    starts = _physical_line_starts(source)
    index = bisect_right(starts, offset) - 1
    index = min(index, max(0, len(starts) - 2))
    return SourceLocation(index + 1, offset - starts[index] + 1)


def _public_location(location: object) -> SourceLocation:
    line = getattr(location, "line", None)
    column = getattr(location, "column", None)
    if line is None or column is None:
        raise AnalysisError(
            "pragma source location is incomplete",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    return SourceLocation(line, column)


def _mask_source_pragmas(source: str) -> tuple[str, tuple[_SourcePragma, ...]]:
    """Replace pragma directives with inert same-width markers.

    Keeping every physical offset stable lets the ordinary concrete preprocessor
    decide reachability. Active markers survive; markers in discarded branches
    are blanked by the existing conditional-selection machinery.
    """
    physical = source.splitlines(keepends=True)
    logical = list(logical_lines(source))
    starts = _physical_line_starts(source)
    characters = list(source)
    pragmas: list[_SourcePragma] = []

    for index, line in enumerate(logical):
        match = re.match(r"^\s*#\s*pragma\b(.*)$", line.text, re.DOTALL)
        if match is None:
            continue
        end_line = (
            logical[index + 1].start_line - 1
            if index + 1 < len(logical)
            else len(physical)
        )
        hash_index = line.text.find("#")
        if hash_index < 0 or hash_index >= len(line.locations):
            raise AnalysisError(
                "cannot locate #pragma directive",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        location = _public_location(line.locations[hash_index])
        marker_offset = starts[location.line - 1] + location.column - 1
        span_start = starts[line.start_line - 1]
        span_end = starts[end_line]
        for position in range(span_start, span_end):
            if source[position] not in "\r\n":
                characters[position] = " "
        # '#pragma' guarantees at least two writable characters beginning at '#'.
        characters[marker_offset] = "0"
        characters[marker_offset + 1] = ";"
        pragmas.append(_SourcePragma(
            match.group(1).strip(),
            location,
            marker_offset,
            line.start_line,
            end_line,
        ))

    return "".join(characters), tuple(pragmas)


def _output_offset_for_source(
    source_map: tuple[SourceMapping, ...],
    source_offset: int,
) -> int | None:
    for mapping in source_map:
        if not (mapping.source_start <= source_offset < mapping.source_end):
            continue
        if mapping.expanded:
            return mapping.output_start
        output = mapping.output_start + source_offset - mapping.source_start
        if output < mapping.output_end:
            return output
        return None
    return None


def _location_for_output(
    source: str,
    source_map: tuple[SourceMapping, ...],
    output_offset: int,
) -> SourceLocation:
    for mapping in source_map:
        if not (mapping.output_start <= output_offset < mapping.output_end):
            continue
        if mapping.expanded:
            return mapping.start
        source_offset = mapping.source_start + output_offset - mapping.output_start
        return _source_location(source, source_offset)
    return SourceLocation(1, 1)


def _destringize_pragma(literal: str) -> str:
    match = re.fullmatch(
        r'(?:u8|u|U|L)?"((?:\\.|[^"\\])*)"',
        literal,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("_Pragma expects one ordinary string literal")
    return re.sub(r'\\(["\\])', r'\1', match.group(1))


def _scan_operator_pragmas(
    output: str,
    source: str,
    source_map: tuple[SourceMapping, ...],
    filename: str | None,
) -> tuple[_RenderedPragma, ...]:
    tokens = tokenize(output)
    found: list[_RenderedPragma] = []
    index = 0

    def significant(cursor: int) -> int:
        while cursor < len(tokens) and tokens[cursor].kind in {"space", "comment"}:
            cursor += 1
        return cursor

    while index < len(tokens):
        token = tokens[index]
        if token.kind != "identifier" or token.text != "_Pragma":
            index += 1
            continue

        location = _location_for_output(source, source_map, token.start)
        opening_index = significant(index + 1)
        if opening_index >= len(tokens) or tokens[opening_index].text != "(":
            raise _MalformedPragma("_Pragma expects a parenthesized string literal", location)
        literal_index = significant(opening_index + 1)
        if literal_index >= len(tokens):
            raise _MalformedPragma("_Pragma expects one ordinary string literal", location)
        literal = tokens[literal_index]
        try:
            payload = _destringize_pragma(literal.text)
        except ValueError as error:
            raise _MalformedPragma(str(error), location) from error
        closing_index = significant(literal_index + 1)
        if closing_index >= len(tokens) or tokens[closing_index].text != ")":
            raise _MalformedPragma("_Pragma expects exactly one string literal operand", location)

        closing = tokens[closing_index]
        found.append(_RenderedPragma(
            Pragma(payload, PragmaOrigin.OPERATOR, location, filename),
            token.start,
            closing.end,
        ))
        index = closing_index + 1

    return tuple(found)


def _source_pragma_events(
    pragmas: tuple[_SourcePragma, ...],
    result: PreprocessResult,
    filename: str | None,
) -> tuple[_RenderedPragma, ...]:
    if result.source is None or result.source_map is None:
        return ()
    found: list[_RenderedPragma] = []
    for pragma in pragmas:
        output_offset = _output_offset_for_source(result.source_map, pragma.marker_offset)
        if output_offset is None or output_offset >= len(result.source):
            continue
        if result.source[output_offset] != "0":
            continue
        found.append(_RenderedPragma(
            Pragma(pragma.payload, PragmaOrigin.DIRECTIVE, pragma.location, filename),
            output_offset,
            min(output_offset + 2, len(result.source)),
            tuple(range(pragma.start_line, pragma.end_line + 1)),
        ))
    return tuple(found)


def _unsupported_pragma(
    filename: str | None,
    pragma: Pragma,
    *,
    unknown_handler: bool,
) -> PreprocessResult:
    if unknown_handler:
        message = "reachable pragma requires caller-provided pragma handling"
    else:
        message = "reachable pragma is unsupported by the caller's pragma handler"
    if pragma.payload:
        message += f": {pragma.payload}"
    return PreprocessResult(
        None,
        filename,
        (PreprocessDiagnostic(
            ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
            message,
            pragma.location,
        ),),
    )


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    configuration: MacroConfiguration | None = None,
    context: PreprocessingContext | None = None,
    include_query: IncludeQueryProvider | None = None,
    pragma_handler: PragmaHandler | None = None,
    options: AnalysisOptions | None = None,
) -> PreprocessResult:
    """Concrete preprocessing with caller-owned pragma semantics.

    cpre recognizes reachable standard ``#pragma`` directives and ``_Pragma``
    operators, including operators exposed by ordinary macro expansion. Both
    forms are delivered through ``pragma_handler`` as :class:`Pragma` values.
    Returning :attr:`PragmaDisposition.CONSUME` removes the pragma from canonical
    output. ``UNSUPPORTED``, ``None``, or the absence of a handler preserves the
    conservative atomic-incomplete contract. cpre does not implement vendor or
    compiler pragma meaning.
    """
    if pragma_handler is not None and not callable(pragma_handler):
        raise AnalysisError(
            "pragma_handler must be callable",
            code=ErrorCode.INVALID_CONFIGURATION,
        )

    transformed, source_pragmas = _mask_source_pragmas(source)
    result = _include_preprocess_source(
        transformed,
        filename=filename,
        assumptions=assumptions,
        configuration=configuration,
        context=context,
        include_query=include_query,
        options=options,
    )
    if not result.complete or result.source is None or result.source_map is None:
        return result

    try:
        operator_pragmas = _scan_operator_pragmas(
            result.source,
            source,
            result.source_map,
            filename,
        )
    except _MalformedPragma as error:
        return PreprocessResult(
            None,
            filename,
            (PreprocessDiagnostic(
                ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                str(error),
                error.location,
            ),),
        )

    rendered = [
        *_source_pragma_events(source_pragmas, result, filename),
        *operator_pragmas,
    ]
    rendered.sort(key=lambda item: item.output_start)
    if not rendered:
        return result

    for item in rendered:
        if pragma_handler is None:
            return _unsupported_pragma(filename, item.pragma, unknown_handler=True)
        disposition = pragma_handler(item.pragma)
        if disposition in {None, PragmaDisposition.UNSUPPORTED}:
            return _unsupported_pragma(filename, item.pragma, unknown_handler=False)
        if disposition is not PragmaDisposition.CONSUME:
            raise AnalysisError(
                "pragma_handler must return PragmaDisposition.CONSUME, "
                "PragmaDisposition.UNSUPPORTED, or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=item.pragma.location,
            )

    characters = list(result.source)
    removed_lines = set(result.removed_lines or ())
    for item in rendered:
        for position in range(item.output_start, item.output_end):
            if characters[position] not in "\r\n":
                characters[position] = " "
        removed_lines.update(item.source_lines)

    return replace(
        result,
        source="".join(characters),
        removed_lines=frozenset(removed_lines),
    )


# Importing the package initializes this module after both lower-level wrappers.
# Keep documented submodule imports on the same public pragma-aware entry point.
from . import include_queries as _include_queries_module
from . import preprocessing as _preprocessing_module

_include_queries_module.preprocess_source = preprocess_source
_preprocessing_module.preprocess_source = preprocess_source


__all__ = [
    "Pragma",
    "PragmaDisposition",
    "PragmaHandler",
    "PragmaOrigin",
    "preprocess_source",
]
