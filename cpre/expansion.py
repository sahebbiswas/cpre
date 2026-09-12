"""Token-aware, bounded macro expansion and physical source mappings."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, replace
from typing import Generator

from .errors import SourceLocation
from .macros import MacroDefinition, MacroEnvironment
from .robdd import AnalysisBudget, AnalysisLimitExceeded

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
    hidden: frozenset[str] = frozenset()
    generated: bool = False


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


class ExpansionError(ValueError):
    """An unsupported or malformed invocation encountered during expansion."""


class Expansion:
    def __init__(self, source: str, budget: AnalysisBudget):
        self.source = source
        self.budget = budget
        self.tokens = tokenize(source)
        self.starts = [token.start for token in self.tokens]
        self.trailing_function: str | None = None
        self.pending_start: int | None = None
        self.pending_end = 0
        self.current_offset = 0
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

    def _replacement(self, definition: MacroDefinition) -> list[Token]:
        text = definition.replacement
        if text not in self.cache:
            tokens = [t for t in tokenize(text) if t.kind not in {'space', 'comment'}]
            if any(t.kind == 'other' and t.text in {'#', '##', '%:', '%:%:'} for t in tokens):
                raise ExpansionError(f'stringification/token pasting is unsupported in {definition.name}')
            if any(t.kind == 'identifier' and t.text == '__VA_OPT__' for t in tokens):
                raise ExpansionError(f'__VA_OPT__ is unsupported in {definition.name}')
            self.cache[text] = tokens
        return self.cache[text]

    def _arguments(self, pending: deque[Token], name: str) -> tuple[list[list[Token]], Token]:
        pending.popleft()  # opening parenthesis
        arguments = [[]]
        depth = 0
        while pending:
            token = pending.popleft()
            self.budget.consume()
            if token.text == ')' and depth == 0:
                return arguments, token
            if token.text == ',' and depth == 0:
                arguments.append([])
                continue
            if token.text == '(':
                depth += 1
            elif token.text == ')':
                depth -= 1
            arguments[-1].append(token)
        raise ExpansionError(f'unterminated invocation of {name} (possibly interrupted by a directive)')

    def _rescan(self, tokens: list[Token], environment: MacroEnvironment
                ) -> Generator[list[Token], list[Token], list[Token]]:
        """Yield argument prescans to an explicit trampoline, never Python recursion.

        Per-token suppression survives argument substitution. Function replacement
        suppression uses the intersection at the invocation's name and closing
        parenthesis, so aliases can form calls with following source tokens.
        """
        pending = deque(tokens)
        emitted = []
        while pending:
            token = pending.popleft()
            self.current_offset = token.start
            self.budget.consume()
            state = environment.get(token.text) if token.kind == 'identifier' else None
            definition = state.definition if state is not None else None
            if state is not None and definition is None and state.defined is not False and (
                    state.defined is True or state.value is not None):
                raise ExpansionError(f'replacement text for assumed macro {token.text} is unknown')
            invoked = definition is not None and (definition.parameters is None or (
                pending and pending[0].text == '('))
            if not invoked or token.text in token.hidden:
                if token.generated:
                    self.budget.work += max(0, len(token.text) - 1)
                    self.budget.consume()
                emitted.append(token)
                continue

            body = self._replacement(definition)
            end = token.end
            hidden = token.hidden | {token.text}
            bindings = {}
            if definition.parameters is not None:
                arguments, closing = self._arguments(pending, definition.name)
                end = max(end, closing.end)
                hidden = (token.hidden & closing.hidden) | {token.text}
                parameters = definition.parameters
                if not parameters and not definition.variadic and arguments == [[]]:
                    arguments = []
                if definition.variadic:
                    # Require the separating comma for named + variadic macros.
                    # Omitted variadic arguments are a separate language-version
                    # feature; explicitly empty variadic arguments are supported.
                    if len(arguments) < len(parameters) + 1:
                        raise ExpansionError(f'variadic invocation {definition.name} requires a variadic argument (which may be empty)')
                    extra = []
                    for index, argument in enumerate(arguments[len(parameters):]):
                        if index:
                            extra.append(Token(',', 'other', token.start, end))
                        extra.extend(argument)
                    arguments = arguments[:len(parameters)] + [extra]
                    parameters = (*parameters, '__VA_ARGS__')
                elif len(arguments) != len(parameters):
                    raise ExpansionError(f'{definition.name} expects {len(parameters)} arguments, got {len(arguments)}')
                used = {part.text for part in body if part.kind == 'identifier'}
                for parameter, argument in zip(parameters, arguments):
                    if parameter in used:
                        bindings[parameter] = yield argument
                self.current_offset = token.start

            replacement = []
            for part in body:
                values = bindings.get(part.text, [part]) if part.kind == 'identifier' else [part]
                for value in values:
                    self.budget.consume()
                    if value.kind != 'empty':
                        replacement.append(replace(value, start=token.start, end=end,
                                                   hidden=value.hidden | hidden, generated=True))
            if replacement:
                pending.extendleft(reversed(replacement))
            else:
                emitted.append(Token('', 'empty', token.start, end, hidden, True))
        return emitted

    def _expand(self, tokens: list[Token], environment: MacroEnvironment) -> list[Token]:
        stack = [self._rescan(tokens, environment)]
        result = None
        while stack:
            try:
                arguments = stack[-1].send(result)
            except StopIteration as finished:
                stack.pop()
                result = finished.value
            else:
                stack.append(self._rescan(arguments, environment))
                result = None
        return result

    def line(self, start: int, end: int) -> None:
        # Buffer adjacent ordinary lines so invocations may span physical lines.
        # The caller flushes before directives mutate the macro environment.
        if self.pending_start is None:
            self.pending_start = start
        self.pending_end = end

    def flush(self, environment: MacroEnvironment) -> None:
        if self.pending_start is None:
            return
        first = bisect_right(self.starts, self.pending_start - 1)
        last = bisect_right(self.starts, self.pending_end - 1)
        original = [t for t in self.tokens[first:last] if t.kind not in {'space', 'comment'}]
        self.pending_start = None
        try:
            expanded = self._expand(original, environment)
        except AnalysisLimitExceeded as error:
            error.line = self.location(self.current_offset).line
            raise
        significant = [t for t in expanded if t.kind != 'empty']
        if significant:
            if self.trailing_function is not None and significant[0].text == '(':
                self.current_offset = significant[0].start
                raise ExpansionError('macro invocation crossing a preprocessing directive is unsupported')
            final = significant[-1]
            definition = environment.get(final.text).definition if final.kind == 'identifier' else None
            self.trailing_function = (final.text if definition is not None and definition.parameters is not None
                                      and final.text not in final.hidden else None)

        # Merge overlapping invocation origins (e.g. A -> value F followed by
        # source arguments). Untouched source between separate uses stays exact.
        start = end = None
        pieces = []

        def finish() -> None:
            if start is not None:
                replacement = ' ' + ' '.join(pieces) + ' '
                replacement += ''.join(re.findall(r'\r\n|\r|\n', self.source[start:end]))
                self.edits.append((start, end, replacement))

        for token in expanded:
            if not token.generated:
                finish()
                start = end = None
                pieces = []
                continue
            if start is None or token.start >= end:
                finish()
                start, end = token.start, token.end
                pieces = []
            else:
                end = max(end, token.end)
            if token.kind != 'empty':
                pieces.append(token.text)
        finish()

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
