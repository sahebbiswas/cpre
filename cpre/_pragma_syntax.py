"""Standard pragma syntax shared by the core preprocessor and pragma dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import SourceMapping
from .lexer import logical_lines, tokenize


@dataclass(frozen=True)
class _SourcePragma:
    payload: str
    location: SourceLocation
    marker_offset: int
    start_line: int
    end_line: int


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
    characters = list(source)
    pragmas: list[_SourcePragma] = []

    for line in logical_lines(source):
        directive = line.directive
        if directive is None or directive.name != "pragma":
            continue
        payload = _normalize_pragma_payload(line.text[directive.name_end :])
        if payload in preserve:
            continue
        location = _public_location(line.locations[directive.hash_index])
        marker_offset = line.physical_offset(directive.hash_index)
        for position in range(line.start_offset, line.end_offset):
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
                line.end_line,
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
