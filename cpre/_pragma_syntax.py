"""Standard pragma syntax shared by the core preprocessor and pragma dispatch."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import SourceMapping, tokenize
from .parser import logical_lines


@dataclass(frozen=True)
class _SourcePragma:
    payload: str
    location: SourceLocation
    marker_offset: int
    start_line: int
    end_line: int


def _physical_line_starts(source: str) -> list[int]:
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _public_location(location: object) -> SourceLocation:
    line = getattr(location, "line", None)
    column = getattr(location, "column", None)
    if line is None or column is None:
        raise AnalysisError(
            "pragma source location is incomplete",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    return SourceLocation(line, column)


def _normalize_pragma_payload(payload: str) -> str:
    """Return stable phase-3 spelling for host dispatch.

    Comments become whitespace and physical inter-token whitespace is normalized,
    while every non-whitespace preprocessing token keeps its exact spelling.
    """
    parts: list[str] = []
    pending_space = False
    for token in tokenize(payload):
        if token.kind in {"space", "comment"}:
            pending_space = True
            continue
        if pending_space and parts:
            parts.append(" ")
        parts.append(token.text)
        pending_space = False
    return "".join(parts)


def _mask_source_pragmas(
    source: str,
    preserve: frozenset[str] = frozenset(),
) -> tuple[str, tuple[_SourcePragma, ...]]:
    """Replace pragma directives with inert same-width markers.

    Pragmas whose normalized payload is in ``preserve`` are left as directives.

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
        end_line = logical[index + 1].start_line - 1 if index + 1 < len(logical) else len(physical)
        hash_index = line.text.find("#")
        if hash_index < 0 or hash_index >= len(line.locations):
            raise AnalysisError(
                "cannot locate #pragma directive",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        payload = _normalize_pragma_payload(match.group(1))
        if payload in preserve:
            continue
        location = _public_location(line.locations[hash_index])
        assert location.column is not None  # _public_location rejects a missing column
        marker_offset = starts[location.line - 1] + location.column - 1
        span_start = starts[line.start_line - 1]
        span_end = starts[end_line]
        for position in range(span_start, span_end):
            if source[position] not in "\r\n":
                characters[position] = " "
        # '#pragma' guarantees at least two writable characters beginning at '#'.
        characters[marker_offset] = "0"
        characters[marker_offset + 1] = ";"
        pragmas.append(
            _SourcePragma(
                payload,
                location,
                marker_offset,
                line.start_line,
                end_line,
            )
        )

    return "".join(characters), tuple(pragmas)


def _output_offset_for_source(
    source_map: tuple[SourceMapping, ...],
    source_offset: int,
    source_identity: str | None = None,
) -> int | None:
    """Map a source offset of one source identity to its canonical output offset."""
    for mapping in source_map:
        if mapping.source_identity != source_identity:
            continue
        if not (mapping.source_start <= source_offset < mapping.source_end):
            continue
        if mapping.expanded:
            return mapping.output_start
        output = mapping.output_start + source_offset - mapping.source_start
        if output < mapping.output_end:
            return output
        return None
    return None
