"""The one lexical front end shared by every cpre component.

This module is the only place that decides what a line splice, a comment, a
literal, a directive and a preprocessing token are. The parser, the structural
parser, macro expansion, configuration, coverage, unknown-macro discovery,
pragma handling and the core preprocessor all read source through it, so they
cannot disagree about where a directive begins or ends.

It implements translation phases 1 to 3 the way GCC and Clang do:

* **Line model.** A physical line ends at ``\\r\\n``, a lone ``\\r`` or ``\\n``
  (see :mod:`cpre._lines`).
* **Splicing.** A backslash, optional horizontal whitespace (space, tab, form
  feed, vertical tab) and a line break are removed. The compilers warn about the
  whitespace form and carry on; so does cpre. Splicing comes first, so a comment
  delimiter or a token may be split by one.
* **Comments.** A comment is one space. A line break inside a comment is not a
  line break: the comment's lines and the text after it form one logical line.
  An unterminated block comment runs to the end of the source.
* **Literals.** String and character literals, with any encoding prefix, end at
  their closing quote or at the end of the line; a quote with no partner on its
  line is an ordinary character. Raw string literals may span lines.
* **Numbers.** A preprocessing number owns its exponent signs and C++ digit
  separators, so ``1'024`` does not start a character literal.

Everything is addressed twice: by an offset into the spliced text and by the
physical offset in the original source that it came from (:class:`OffsetMap`).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from functools import cached_property, lru_cache

from ._lines import LINE_BREAK, line_starts, split_lines
from .model import SourceLocation

HORIZONTAL_WHITESPACE = " \t\f\v"
DIRECTIVE_LEADING_WHITESPACE = r"[ \t\f\v]"
DIRECTIVE_PREFIX = rf"^{DIRECTIVE_LEADING_WHITESPACE}*#"
CONDITIONAL_DIRECTIVES = frozenset(
    ("if", "ifdef", "ifndef", "elif", "elifdef", "elifndef", "else", "endif")
)

# The single definition of a line splice.
SPLICE = re.compile(r"\\[ \t\f\v]*(?:\r\n|\r|\n)")

# Each construct is defined once here and assembled into the region scanner (comments,
# literals, numbers: what must be skipped to find directives) and the full token
# scanner. ``(?<!\w)`` keeps an encoding prefix or a number from starting inside an
# identifier, which the token scanner gets for free by consuming identifiers whole.
_ENCODING = r"(?:u8|u|U|L)"
_RAW_STRING = rf'(?<!\w){_ENCODING}?R"(?P<delimiter>[^ ()\\\t\r\n]{{0,16}})\(.*?\)(?P=delimiter)"'
_QUOTED = r'"(?:\\[^\r\n]|[^"\\\r\n])*"' + r"|'(?:\\[^\r\n]|[^'\\\r\n])*'"
_LITERAL = rf"(?:(?<!\w){_ENCODING})?(?:{_QUOTED})"
_COMMENT = r"/\*.*?(?:\*/|\Z)|//[^\r\n]*"
_NUMBER = r"(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[\w.]|'\w)*"

_REGIONS = re.compile(
    rf"(?P<raw>{_RAW_STRING})|(?P<literal>{_LITERAL})|(?P<comment>{_COMMENT})"
    rf"|(?<!\w)(?P<number>{_NUMBER})",
    re.DOTALL,
)
_TOKEN = re.compile(
    rf"(?P<raw>{_RAW_STRING})|(?P<literal>{_LITERAL})|(?P<comment>{_COMMENT})"
    rf"|(?P<number>{_NUMBER})"
    r"|(?P<identifier>[A-Za-z_]\w*)|(?P<space>\s+)"
    r"|(?P<other>%:%:|>>=|<<=|->\*|\.\.\.|##|::|\.\*|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||"
    r"\+=|-=|\*=|/=|%=|&=|\^=|\|=|<:|:>|<%|%>|%:|.)",
    re.DOTALL,
)
_MASKED = re.compile(r"[^\r\n]")
_DIRECTIVE_HEAD = re.compile(
    rf"{DIRECTIVE_LEADING_WHITESPACE}*#{DIRECTIVE_LEADING_WHITESPACE}*(?P<name>[A-Za-z_]\w*)?"
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


class OffsetMap:
    """Maps offsets in text derived from a source back to physical offsets in it.

    The derived text is the source with some spans removed (line splices, line
    breaks inside comments). Storing only the removed spans keeps the map small
    however long the source is.
    """

    __slots__ = ("_cuts", "_shifts")

    def __init__(self, removed: Sequence[tuple[int, int]] = ()) -> None:
        # ``removed`` holds physical (start, end) spans in increasing order.
        cuts: list[int] = []
        shifts = [0]
        total = 0
        for start, end in removed:
            cuts.append(start - total)
            total += end - start
            shifts.append(total)
        self._cuts = cuts
        self._shifts = shifts

    def physical(self, index: int) -> int:
        """Physical offset of the character at derived ``index``."""
        return index + self._shifts[bisect_right(self._cuts, index)]

    def physical_end(self, end: int) -> int:
        """Physical exclusive end of a derived range that ends at ``end`` (exclusive).

        The range ends right after its last character, so a splice that follows the
        range is not part of it.
        """
        return self.physical(end - 1) + 1


@dataclass(frozen=True)
class Token:
    """One preprocessing token with physical offsets; ``hidden`` and ``generated`` are
    annotations added during macro expansion.

    ``space_before`` records that whitespace or a comment preceded the token where it was
    written. It travels with the token through macro expansion, which is how ``#``
    knows where to put a space (C11 6.10.3.2p2). It is not part of a token's identity.
    """

    text: str
    kind: str
    start: int
    end: int
    hidden: frozenset[str] = frozenset()
    generated: bool = False
    space_before: bool = field(default=False, compare=False)


def _splice(source: str) -> tuple[str, list[tuple[int, int]]]:
    """``source`` with every splice removed, and the physical spans that were removed."""
    spans = [match.span() for match in SPLICE.finditer(source)]
    if not spans:
        return source, spans
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(source[cursor:start])
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts), spans


def splice(source: str) -> str:
    """``source`` with every backslash-newline (and backslash-space-newline) removed."""
    return _splice(source)[0]


def scan(text: str, start: int = 0) -> Iterator[Token]:
    """Preprocessing tokens of already spliced ``text``, with offsets into ``text``."""
    space = False
    for match in _TOKEN.finditer(text, start):
        kind = match.lastgroup or "other"
        if kind in {"space", "comment"}:
            space = True
            yield Token(match.group(), kind, match.start(), match.end())
            continue
        yield Token(match.group(), kind, match.start(), match.end(), space_before=space)
        space = False


def tokenize(source: str) -> list[Token]:
    """Splice ``source`` and scan it; token offsets are physical offsets in ``source``."""
    text, spans = _splice(source)
    if not spans:
        return list(scan(text))
    offsets = OffsetMap(spans)
    return [
        Token(
            token.text,
            token.kind,
            offsets.physical(token.start),
            offsets.physical_end(token.end),
            space_before=token.space_before,
        )
        for token in scan(text)
    ]


def strip_comments(source: str) -> str:
    """Blank out comments, keeping every character position and line break."""
    text, spans = _splice(source)
    offsets = OffsetMap(spans)
    characters = list(source)
    for match in _REGIONS.finditer(text):
        if match.lastgroup != "comment":
            continue
        start = offsets.physical(match.start())
        end = offsets.physical_end(match.end())
        comment = match.group()
        if match.end() == len(text) and not (
            len(comment) >= 4 and comment.startswith("/*") and comment.endswith("*/")
        ):
            # A comment that runs to the end of the source also covers a final splice.
            end = len(source)
        for position in range(start, end):
            if characters[position] not in "\r\n":
                characters[position] = " "
    return "".join(characters)


@dataclass(frozen=True)
class Directive:
    """The head of a directive line: ``#`` and the name that follows it, if any."""

    name: str | None
    hash_index: int
    name_end: int


def directive_head(text: str) -> Directive | None:
    """Recognize ``#`` and an optional directive name at the start of ``text``.

    Only space, tab, form feed and vertical tab may come before or after the ``#``.
    ``name`` is ``None`` for the null directive and for ``# 12 "file"`` style lines.
    ``name_end`` is where the operand starts.
    """
    match = _DIRECTIVE_HEAD.match(text)
    if match is None:
        return None
    name = match.group("name")
    return Directive(name, text.index("#", 0, match.end()), match.end())


def macro_name(operand: str) -> str | None:
    """The macro name that opens the operand of ``#define`` or ``#undef``.

    Leading whitespace is skipped exactly as the macro grammar skips it.
    """
    match = _IDENTIFIER.match(operand.lstrip())
    return match.group() if match else None


@dataclass(frozen=True, eq=False)
class LogicalLine:
    """One logical line: physical lines joined by splices and by comments that span lines.

    ``text`` is spliced, with each comment replaced by spaces of the same width.
    ``masked`` is ``text`` with the contents of string and character literals hidden,
    which is what directive recognition reads. Offsets and line numbers are physical.
    """

    start_line: int
    end_line: int
    text: str
    masked: str
    start_offset: int
    end_offset: int
    index: int
    lexed: LexedSource

    @cached_property
    def directive(self) -> Directive | None:
        return directive_head(self.masked)

    @cached_property
    def first_line(self) -> int:
        """Physical line of the first retained character.

        ``start_line`` also covers splice-only lines before it, which masking must blank
        but diagnostics must not report.
        """
        if not self.text:
            return self.start_line
        return self.lexed.location(self.index).line or self.start_line

    @cached_property
    def directive_line(self) -> int:
        """Physical line of the directive's ``#`` (``first_line`` for ordinary lines)."""
        directive = self.directive
        if directive is None or not self.text:
            return self.first_line
        return self.lexed.location(self.index + directive.hash_index).line or self.first_line

    @cached_property
    def locations(self) -> tuple[SourceLocation, ...]:
        """Physical location of every character of ``text``."""
        return tuple(self.lexed.location(self.index + offset) for offset in range(len(self.text)))

    def physical_offset(self, index: int) -> int:
        """Physical offset of ``text[index]``."""
        return self.lexed.offsets.physical(self.index + index)

    def physical_end(self, end: int) -> int:
        """Physical exclusive end of the range of ``text`` that ends at ``end``."""
        return self.lexed.offsets.physical_end(self.index + end)


@dataclass(frozen=True, eq=False)
class LexedSource:
    """Translation phases 1 to 3 of one source, with every position mapped back to it."""

    source: str
    text: str
    masked: str
    offsets: OffsetMap
    starts: list[int]

    def location(self, index: int) -> SourceLocation:
        """Physical one-based location of the character at ``text[index]``."""
        offset = self.offsets.physical(index)
        line = bisect_right(self.starts, offset)
        return SourceLocation(line, offset - self.starts[line - 1] + 1)

    @cached_property
    def lines(self) -> tuple[LogicalLine, ...]:
        result: list[LogicalLine] = []
        cursor = 0
        for raw in split_lines(self.text, keepends=True):
            ending = 2 if raw.endswith("\r\n") else 1 if raw.endswith(("\r", "\n")) else 0
            width = len(raw) - ending
            start_offset = self.offsets.physical(cursor - 1) + 1 if cursor else 0
            end_offset = (
                self.offsets.physical(cursor + len(raw) - 1) + 1 if ending else len(self.source)
            )
            result.append(
                LogicalLine(
                    start_line=bisect_right(self.starts, start_offset),
                    end_line=bisect_right(self.starts, end_offset - 1),
                    text=raw[:width],
                    masked=self.masked[cursor : cursor + width],
                    start_offset=start_offset,
                    end_offset=end_offset,
                    index=cursor,
                    lexed=self,
                )
            )
            cursor += len(raw)
        return tuple(result)


@lru_cache(maxsize=8)
def lex(source: str) -> LexedSource:
    """Run translation phases 1 to 3 over ``source`` (cached: sources are immutable)."""
    text, splices = _splice(source)
    to_physical = OffsetMap(splices)
    removed = list(splices)
    logical: list[str] = []
    masked: list[str] = []
    cursor = 0
    for match in _REGIONS.finditer(text):
        kind = match.lastgroup
        if kind == "number":
            continue
        start, end = match.span()
        between = text[cursor:start]
        logical.append(between)
        masked.append(between)
        cursor = end
        if kind == "comment":
            # A comment is one space, and a line break inside it is not a line break.
            dropped = 0
            for line_break in LINE_BREAK.finditer(text, start, end):
                removed.append(
                    (
                        to_physical.physical(line_break.start()),
                        to_physical.physical_end(line_break.end()),
                    )
                )
                dropped += line_break.end() - line_break.start()
            blank = " " * (end - start - dropped)
            logical.append(blank)
            masked.append(blank)
        else:
            body = match.group()
            logical.append(body)
            masked.append(_MASKED.sub("@", body))
    logical.append(text[cursor:])
    masked.append(text[cursor:])
    removed.sort()
    return LexedSource(
        source=source,
        text="".join(logical),
        masked="".join(masked),
        offsets=OffsetMap(removed),
        starts=line_starts(source),
    )


def logical_lines(source: str) -> Iterator[LogicalLine]:
    """Yield the logical lines of ``source``."""
    return iter(lex(source).lines)


__all__ = [
    "CONDITIONAL_DIRECTIVES",
    "DIRECTIVE_LEADING_WHITESPACE",
    "DIRECTIVE_PREFIX",
    "HORIZONTAL_WHITESPACE",
    "SPLICE",
    "Directive",
    "LexedSource",
    "LogicalLine",
    "OffsetMap",
    "Token",
    "directive_head",
    "lex",
    "logical_lines",
    "macro_name",
    "scan",
    "splice",
    "strip_comments",
    "tokenize",
]
