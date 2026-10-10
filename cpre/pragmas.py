"""Host-owned handling for standard pragma syntax and ``_Pragma``."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from ._pragma_syntax import (
    _mask_source_pragmas,
    _normalize_pragma_payload,
    _output_offset_for_source,
    _physical_line_starts,
    _public_location,
    _SourcePragma,
)
from .api import AnalysisOptions, MacroAssumptions
from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import SourceMapping, tokenize
from .include_queries import IncludeQueryProvider
from .include_queries import preprocess_source as _include_preprocess_source
from .includes import DEFAULT_MAX_INCLUDE_DEPTH, IncludeResolver
from .preprocessing import PreprocessDiagnostic, PreprocessingContext, PreprocessResult


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
class _RenderedPragma:
    pragma: Pragma
    output_start: int
    output_end: int
    output_lines: tuple[int, ...] = ()
    source_identity: str | None = None


class _MalformedPragma(ValueError):
    def __init__(
        self,
        message: str,
        location: SourceLocation,
        source_identity: str | None = None,
    ) -> None:
        super().__init__(message)
        self.location = location
        self.source_identity = source_identity


def _source_location(source: str, offset: int) -> SourceLocation:
    starts = _physical_line_starts(source)
    index = bisect_right(starts, offset) - 1
    index = min(index, max(0, len(starts) - 2))
    return SourceLocation(index + 1, offset - starts[index] + 1)


def _output_line(output: str, offset: int) -> int:
    """Return the one-based canonical output line containing ``offset``."""
    lines = output[:offset].splitlines(keepends=True)
    if not lines:
        return 1
    last = lines[-1]
    return len(lines) + (1 if last.splitlines()[0] != last else 0)


def _location_for_output(
    source: str,
    output: str,
    source_map: tuple[SourceMapping, ...],
    output_offset: int,
) -> tuple[SourceLocation, str | None]:
    """Return the physical location and source identity behind an output offset."""
    for mapping in source_map:
        if not (mapping.output_start <= output_offset < mapping.output_end):
            continue
        if mapping.expanded:
            return mapping.start, mapping.source_identity
        if mapping.source_identity is None:
            source_offset = mapping.source_start + output_offset - mapping.output_start
            return _source_location(source, source_offset), None
        # Unexpanded included text is copied verbatim, so the output spelling between
        # the mapping start and the offset locates it within the included source.
        assert mapping.start.column is not None
        lines = output[mapping.output_start : output_offset].splitlines(keepends=True)
        if not lines:
            location = mapping.start
        elif lines[-1].splitlines()[0] != lines[-1]:
            location = SourceLocation(mapping.start.line + len(lines), 1)
        elif len(lines) == 1:
            location = SourceLocation(mapping.start.line, mapping.start.column + len(lines[0]))
        else:
            location = SourceLocation(mapping.start.line + len(lines) - 1, len(lines[-1]) + 1)
        return location, mapping.source_identity
    return SourceLocation(1, 1), None


def _destringize_pragma(literal: str) -> str:
    match = re.fullmatch(
        r'(?:u8|u|U|L)?"((?:\\.|[^"\\])*)"',
        literal,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("_Pragma expects one ordinary string literal")
    return re.sub(r'\\(["\\])', r"\1", match.group(1))


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

        # _Pragma is a standard phase-4 preprocessing operator. Treat a surviving
        # token that does not form the required unary expression as malformed
        # reserved preprocessing syntax rather than silently passing it through.
        location, identity = _location_for_output(source, output, source_map, token.start)
        opening_index = significant(index + 1)
        if opening_index >= len(tokens) or tokens[opening_index].text != "(":
            raise _MalformedPragma(
                "_Pragma expects a parenthesized string literal", location, identity
            )
        literal_index = significant(opening_index + 1)
        if literal_index >= len(tokens):
            raise _MalformedPragma(
                "_Pragma expects one ordinary string literal", location, identity
            )
        literal = tokens[literal_index]
        try:
            payload = _normalize_pragma_payload(_destringize_pragma(literal.text))
        except ValueError as error:
            raise _MalformedPragma(str(error), location, identity) from error
        closing_index = significant(literal_index + 1)
        if closing_index >= len(tokens) or tokens[closing_index].text != ")":
            raise _MalformedPragma(
                "_Pragma expects exactly one string literal operand", location, identity
            )

        closing = tokens[closing_index]
        found.append(
            _RenderedPragma(
                Pragma(
                    payload,
                    PragmaOrigin.OPERATOR,
                    location,
                    identity if identity is not None else filename,
                ),
                token.start,
                closing.end,
                source_identity=identity,
            )
        )
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
        first_line = _output_line(result.source, output_offset)
        found.append(
            _RenderedPragma(
                Pragma(pragma.payload, PragmaOrigin.DIRECTIVE, pragma.location, filename),
                output_offset,
                min(output_offset + 2, len(result.source)),
                tuple(range(first_line, first_line + pragma.end_line - pragma.start_line + 1)),
            )
        )
    for included in result._included_pragmas:
        first_line = _output_line(result.source, included.output_start)
        found.append(
            _RenderedPragma(
                Pragma(
                    included.payload,
                    PragmaOrigin.DIRECTIVE,
                    included.location,
                    included.source_identity,
                ),
                included.output_start,
                min(included.output_start + 2, len(result.source)),
                tuple(range(first_line, first_line + included.line_count)),
                included.source_identity,
            )
        )
    return tuple(found)


def _unsupported_pragma(
    filename: str | None,
    item: _RenderedPragma,
    *,
    unknown_handler: bool,
) -> PreprocessResult:
    pragma = item.pragma
    location = pragma.location
    if unknown_handler and pragma.origin is PragmaOrigin.DIRECTIVE:
        # Preserve the pre-extension default diagnostic shape for source pragmas.
        message = "#pragma preprocessing directive is not supported during concrete preprocessing"
        location = SourceLocation(pragma.location.line)
    elif unknown_handler:
        message = "reachable pragma requires caller-provided pragma handling"
    else:
        message = "reachable pragma is unsupported by the caller's pragma handler"
    if pragma.payload and not (unknown_handler and pragma.origin is PragmaOrigin.DIRECTIVE):
        message += f": {pragma.payload}"
    return PreprocessResult(
        None,
        filename,
        (
            PreprocessDiagnostic(
                ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                message,
                location,
                item.source_identity,
            ),
        ),
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
    skip_includes: bool = False,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
    has_include_from_resolver: bool = False,
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
        skip_includes=skip_includes,
        include_resolver=include_resolver,
        max_include_depth=max_include_depth,
        has_include_from_resolver=has_include_from_resolver,
        _dispatch_included_pragmas=True,
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
            (
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                    str(error),
                    error.location,
                    error.source_identity,
                ),
            ),
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
            return _unsupported_pragma(filename, item, unknown_handler=True)
        disposition = pragma_handler(item.pragma)
        if disposition in {None, PragmaDisposition.UNSUPPORTED}:
            return _unsupported_pragma(filename, item, unknown_handler=False)
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
        removed_lines.update(item.output_lines)

    return replace(
        result,
        source="".join(characters),
        removed_lines=frozenset(removed_lines),
    )


__all__ = [
    "Pragma",
    "PragmaDisposition",
    "PragmaHandler",
    "PragmaOrigin",
    "preprocess_source",
]
