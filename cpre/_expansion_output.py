"""Expansion output: source mappings, edit merging and rendering.

The last step of expansion turns the generated tokens of each buffered run of source
lines into edits against the original text, and renders the edited text together with
its source mappings.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from ._lines import LINE_BREAK, line_starts
from .errors import SourceLocation
from .lexer import Token


@dataclass(frozen=True)
class SourceMapping:
    """Half-open character offsets; expanded spans map to the invocation range.

    Unexpanded spans map one-to-one. Locations are one-based physical positions;
    source_end and end are exclusive. Offsets count Python characters, not bytes.
    ``source_identity`` is ``None`` for the primary source and otherwise names the
    resolved included source that the offsets and locations refer to.
    """

    output_start: int
    output_end: int
    source_start: int
    source_end: int
    start: SourceLocation
    end: SourceLocation
    expanded: bool = False
    source_identity: str | None = None


# (start, end, replacement text): the source range an expansion replaces.
Edit = tuple[int, int, str]


class SourceLocator:
    """Converts character offsets of one source into one-based physical locations."""

    def __init__(self, source: str):
        self._line_starts = line_starts(source)

    def location(self, offset: int) -> SourceLocation:
        index = bisect_right(self._line_starts, offset) - 1
        return SourceLocation(index + 1, offset - self._line_starts[index] + 1)


def merged_edits(source: str, expanded: list[Token]) -> list[Edit]:
    """One edit per run of generated tokens whose invocation ranges overlap.

    Overlapping invocation origins (e.g. A -> value F followed by source arguments)
    merge into one edit; untouched source between separate uses stays exact.
    """
    edits: list[Edit] = []
    start = end = 0
    pieces: list[str] = []
    open_run = False

    def close() -> None:
        if open_run:
            replacement = " " + " ".join(pieces) + " "
            replacement += "".join(LINE_BREAK.findall(source[start:end]))
            edits.append((start, end, replacement))

    for token in expanded:
        if not token.generated:
            close()
            open_run = False
            continue
        if not open_run or token.start >= end:
            close()
            start, end = token.start, token.end
            pieces = []
            open_run = True
        else:
            end = max(end, token.end)
        if token.kind != "empty":
            pieces.append(token.text)
    close()
    return edits


def render(
    masked: str, edits: list[Edit], locator: SourceLocator
) -> tuple[str, tuple[SourceMapping, ...]]:
    """Apply ``edits`` to ``masked`` and map every output span back to its source range."""
    pieces: list[str] = []
    mappings: list[SourceMapping] = []
    output_offset = 0

    def append(text: str, start: int, end: int, expanded: bool = False) -> None:
        nonlocal output_offset
        if not text:
            return
        pieces.append(text)
        mappings.append(
            SourceMapping(
                output_offset,
                output_offset + len(text),
                start,
                end,
                locator.location(start),
                locator.location(end),
                expanded,
            )
        )
        output_offset += len(text)

    cursor = 0
    for start, end, text in edits:
        append(masked[cursor:start], cursor, start)
        append(text, start, end, True)
        cursor = end
    append(masked[cursor:], cursor, len(masked))
    return "".join(pieces), tuple(mappings)
