"""Token-aware, bounded object macro expansion and physical source mappings."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from .errors import SourceLocation
from .macros import MacroEnvironment
from .robdd import AnalysisBudget

# Preprocessing numbers must stay whole (including exponent signs and C++ digit
# separators). Encoding prefixes and raw strings belong to their literal token.
_TOKEN = re.compile(
    r'(?P<raw>(?:u8|u|U|L)?R"(?P<delimiter>[^ ()\\\t\r\n]{0,16})\(.*?\)(?P=delimiter)")'
    r'|(?P<literal>(?:u8|u|U|L)?(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'))'
    r'|(?P<comment>/\*.*?\*/|//[^\r\n]*)'
    r'|(?P<number>(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[\w.\'])*)'
    r'|(?P<identifier>[A-Za-z_]\w*)|(?P<space>\s+)'
    r'|(?P<other>%:%:|>>=|<<=|->\*|\.\.\.|##|::|\.\*|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||'
    r'\+=|-=|\*=|/=|%=|&=|\^=|\|=|<:|:>|<%|%>|%:|.)',
    re.DOTALL,
)
_SPLICE = re.compile(r'\\(?:\r\n|\n|\r)')


@dataclass(frozen=True)
class SourceMapping:
    """Half-open character offsets; expanded spans map to the invocation range.

    Unexpanded spans map one-to-one. Locations are one-based physical positions;
    source_end and end are exclusive. Offsets count Python characters, not bytes.
    """

    output_start: int
    output_end: int
    source_start: int
    source_end: int
    start: SourceLocation
    end: SourceLocation
    expanded: bool = False


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    start: int
    end: int


def tokenize(source: str) -> list[Token]:
    """Apply line splicing before lexing, retaining physical offset provenance."""
    offsets = []
    parts = []
    cursor = 0
    for splice in _SPLICE.finditer(source):
        parts.append(source[cursor:splice.start()])
        offsets.extend(range(cursor, splice.start()))
        cursor = splice.end()
    parts.append(source[cursor:])
    offsets.extend(range(cursor, len(source)))
    text = ''.join(parts)
    return [Token(m.group(), m.lastgroup, offsets[m.start()], offsets[m.end() - 1] + 1)
            for m in _TOKEN.finditer(text)]


class Expansion:
    def __init__(self, source: str, budget: AnalysisBudget):
        self.source = source
        self.budget = budget
        self.tokens = tokenize(source)
        self.starts = [token.start for token in self.tokens]
        self.trailing_function: str | None = None
        self.line_starts = [0]
        self.line_starts.extend(m.end() for m in re.finditer(r'\r\n|\r|\n', source))
        self.edits: list[tuple[int, int, str]] = []
        self.cache: dict[str, list[Token]] = {}

    def location(self, offset: int) -> SourceLocation:
        index = bisect_right(self.line_starts, offset) - 1
        return SourceLocation(index + 1, offset - self.line_starts[index] + 1)

    def directive_text(self, start: int, end: int) -> str:
        first = bisect_right(self.starts, start - 1)
        last = bisect_right(self.starts, end - 1)
        return ''.join(' ' if token.kind == 'comment' else token.text
                       for token in self.tokens[first:last]).strip()

    def expand(self, token: Token, environment: MacroEnvironment) -> str | None:
        if token.kind != 'identifier':
            return None
        state = environment.get(token.text)
        if state.definition is None:
            if state.defined is not False and (state.defined is True or state.value is not None):
                raise ValueError(f'replacement text for assumed macro {token.text} is unknown')
            return None
        if state.definition.parameters is not None:
            # A bare name is not an invocation. The caller checks invocation
            # adjacency after object replacements have been rescanned.
            return None
        emitted = []
        # Explicit stack avoids Python recursion limits on long alias chains.
        pending = [(iter([token]), None)]
        disabled: set[str] = set()
        while pending:
            iterator, name = pending[-1]
            current = next(iterator, None)
            if current is None:
                pending.pop()
                if name is not None:
                    disabled.remove(name)
                continue
            self.budget.consume()
            definition = environment.get(current.text).definition if current.kind == 'identifier' else None
            if definition is not None and definition.parameters is None and current.text not in disabled:
                if definition.replacement not in self.cache:
                    self.cache[definition.replacement] = tokenize(definition.replacement)
                replacement = self.cache[definition.replacement]
                if any(t.kind == 'other' and t.text in {'#', '##', '%:', '%:%:'} for t in replacement):
                    raise ValueError(f'stringification/token pasting is unsupported in {current.text}')
                disabled.add(current.text)
                pending.append((iter(replacement), current.text))
            else:
                state = environment.get(current.text)
                if current.kind == 'identifier' and state.definition is None and state.defined is not False and (state.defined is True or state.value is not None):
                    raise ValueError(f'replacement text for assumed macro {current.text} is unknown')
                if current.kind not in {'space', 'comment'}:
                    # Count emitted characters as work as well as visited tokens,
                    # bounding repeated expansion of large literal replacements.
                    self.budget.work += len(current.text) - 1
                    self.budget.consume()
                    emitted.append(current.text)
        # Separators preserve preprocessing-token boundaries, e.g. '+' + '+',
        # '/' + '*', or an empty macro between two punctuation tokens.
        return ' ' + ' '.join(emitted) + ' '

    def line(self, start: int, end: int, environment: MacroEnvironment) -> None:
        first = bisect_right(self.starts, start - 1)
        last = bisect_right(self.starts, end - 1)
        significant = []
        for token in self.tokens[first:last]:
            self.budget.consume()
            replacement = self.expand(token, environment)
            if replacement is not None:
                physical = self.source[token.start:token.end]
                replacement += ''.join(re.findall(r'\r\n|\r|\n', physical))
                self.edits.append((token.start, token.end, replacement))
                significant.extend(t for t in tokenize(replacement) if t.kind not in {'space', 'comment'})
            elif token.kind not in {'space', 'comment'}:
                significant.append(token)
        for token in significant:
            if self.trailing_function is not None and token.text == '(':
                raise ValueError(f'function-like macro invocation {self.trailing_function} is unsupported')
            definition = environment.get(token.text).definition if token.kind == 'identifier' else None
            self.trailing_function = (token.text if definition is not None and definition.parameters is not None
                                      else None)

    def render(self, masked: str) -> tuple[str, tuple[SourceMapping, ...]]:
        pieces = []
        mappings = []
        cursor = 0
        output_offset = 0

        def append(text: str, start: int, end: int, expanded: bool = False) -> None:
            nonlocal output_offset
            if not text:
                return
            pieces.append(text)
            mappings.append(SourceMapping(output_offset, output_offset + len(text), start, end,
                                          self.location(start), self.location(end), expanded))
            output_offset += len(text)

        for start, end, text in self.edits:
            append(masked[cursor:start], cursor, start)
            append(text, start, end, True)
            cursor = end
        append(masked[cursor:], cursor, len(masked))
        return ''.join(pieces), tuple(mappings)
