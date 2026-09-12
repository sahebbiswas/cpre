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
            if any(t.kind == 'identifier' and t.text == '__VA_OPT__' for t in tokens):
                raise ExpansionError(f'__VA_OPT__ is unsupported in {definition.name}')
            self.cache[text] = tokens
        return self.cache[text]

    def _arguments(self, pending: deque[Token], name: str
                   ) -> tuple[list[list[Token]], list[Token], Token]:
        pending.popleft()  # opening parenthesis
        arguments = [[]]
        separators = []
        depth = 0
        while pending:
            token = pending.popleft()
            self.budget.consume()
            if token.text == ')' and depth == 0:
                return arguments, separators, token
            if token.text == ',' and depth == 0:
                separators.append(token)
                arguments.append([])
                continue
            if token.text == '(':
                depth += 1
            elif token.text == ')':
                depth -= 1
            arguments[-1].append(token)
        raise ExpansionError(f'unterminated invocation of {name} (possibly interrupted by a directive)')

    @staticmethod
    def _paste_operator(token: Token) -> bool:
        return token.kind == 'other' and token.text in {'##', '%:%:'}

    @staticmethod
    def _stringify_operator(token: Token) -> bool:
        return token.kind == 'other' and token.text in {'#', '%:'}

    def _stringify(self, tokens: list[Token], start: int, end: int) -> Token:
        """Stringify an unexpanded argument using its invocation spelling."""
        pieces: list[str] = []
        previous: Token | None = None
        for token in tokens:
            if previous is not None:
                gap = ''
                if not previous.generated and not token.generated and previous.end <= token.start:
                    gap = _SPLICE.sub('', self.source[previous.end:token.start])
                if gap and (re.search(r'\s', gap) or '/*' in gap or '//' in gap):
                    pieces.append(' ')
            pieces.append(token.text)
            previous = token
        spelling = ''.join(pieces).strip()
        spelling = spelling.replace('\\', '\\\\').replace('"', '\\"')
        return Token(f'"{spelling}"', 'literal', start, end, generated=True)

    def _paste(self, left: list[Token], right: list[Token], definition: MacroDefinition,
               start: int, end: int) -> list[Token]:
        """Paste the boundary tokens, treating empty arguments as placemarkers."""
        if not left:
            return right
        if not right:
            return left
        text = left[-1].text + right[0].text
        tokens = [token for token in tokenize(text) if token.kind not in {'space', 'comment'}]
        if len(tokens) != 1 or tokens[0].text != text:
            raise ExpansionError(
                f'invalid token paste in {definition.name}: {left[-1].text!r} ## {right[0].text!r}'
            )
        merged = Token(text, tokens[0].kind, start, end,
                       left[-1].hidden | right[0].hidden, True)
        return left[:-1] + [merged] + right[1:]

    def _substitute(self, body: list[Token], definition: MacroDefinition,
                    raw: dict[str, list[Token]], expanded: dict[str, list[Token]],
                    start: int, end: int) -> list[Token]:
        """Apply #/## and parameter substitution before the replacement rescan."""
        parameters = set(raw)
        elements: list[list[Token] | str] = []
        index = 0
        while index < len(body):
            part = body[index]
            if self._stringify_operator(part) and definition.parameters is not None:
                if index + 1 >= len(body):
                    raise ExpansionError(
                        f'stringification operator in {definition.name} is not followed by a parameter'
                    )
                operand = body[index + 1]
                if operand.kind != 'identifier' or operand.text not in parameters:
                    raise ExpansionError(
                        f'stringification operator in {definition.name} is not followed by a parameter'
                    )
                elements.append([self._stringify(raw[operand.text], start, end)])
                index += 2
                continue
            if self._paste_operator(part):
                elements.append('##')
                index += 1
                continue
            if part.kind == 'identifier' and part.text in parameters:
                adjacent_paste = ((index > 0 and self._paste_operator(body[index - 1])) or
                                  (index + 1 < len(body) and self._paste_operator(body[index + 1])))
                elements.append(list(raw[part.text] if adjacent_paste else expanded[part.text]))
            else:
                elements.append([part])
            index += 1

        if not elements:
            return []
        if elements[0] == '##' or elements[-1] == '##':
            raise ExpansionError(f'token paste operator cannot appear at an edge of {definition.name}')
        result = elements[0]
        if not isinstance(result, list):
            raise ExpansionError(f'invalid token paste placement in {definition.name}')
        index = 1
        while index < len(elements):
            item = elements[index]
            if item == '##':
                if index + 1 >= len(elements) or elements[index + 1] == '##':
                    raise ExpansionError(f'invalid token paste placement in {definition.name}')
                right = elements[index + 1]
                assert isinstance(right, list)
                result = self._paste(result, right, definition, start, end)
                index += 2
            else:
                assert isinstance(item, list)
                result.extend(item)
                index += 1
        return result

    def _rescan(self, tokens: list[Token], environment: MacroEnvironment
                ) -> Generator[list[Token], list[Token], list[Token]]:
        """Yield argument prescans to an explicit trampoline, never Python recursion.

        Parameters used by # or ## retain their raw spelling; ordinary uses are
        prescanned. Per-token suppression survives both paths and the result is
        rescanned with following source tokens.
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
            raw_bindings: dict[str, list[Token]] = {}
            expanded_bindings: dict[str, list[Token]] = {}
            if definition.parameters is not None:
                arguments, separators, closing = self._arguments(pending, definition.name)
                end = max(end, closing.end)
                hidden = (token.hidden & closing.hidden) | {token.text}
                parameters = definition.parameters
                if not parameters and not definition.variadic and arguments == [[]]:
                    arguments = []
                if definition.variadic:
                    # Require the separating comma for named + variadic macros.
                    # Omitted variadic arguments remain a separate language-version
                    # feature; explicitly empty variadic arguments are supported.
                    if len(arguments) < len(parameters) + 1:
                        raise ExpansionError(
                            f'variadic invocation {definition.name} requires a variadic argument (which may be empty)'
                        )
                    extra = []
                    variadic_arguments = arguments[len(parameters):]
                    for argument_index, argument in enumerate(variadic_arguments):
                        if argument_index:
                            extra.append(separators[len(parameters) + argument_index - 1])
                        extra.extend(argument)
                    arguments = arguments[:len(parameters)] + [extra]
                    parameters = (*parameters, '__VA_ARGS__')
                elif len(arguments) != len(parameters):
                    raise ExpansionError(f'{definition.name} expects {len(parameters)} arguments, got {len(arguments)}')
                raw_bindings = dict(zip(parameters, arguments))
                for parameter, argument in raw_bindings.items():
                    needs_expansion = False
                    for index, part in enumerate(body):
                        if part.kind != 'identifier' or part.text != parameter:
                            continue
                        if index > 0 and self._stringify_operator(body[index - 1]):
                            continue
                        if index > 0 and self._paste_operator(body[index - 1]):
                            continue
                        if index + 1 < len(body) and self._paste_operator(body[index + 1]):
                            continue
                        needs_expansion = True
                        break
                    expanded_bindings[parameter] = (yield argument) if needs_expansion else argument
                self.current_offset = token.start

            replacement = self._substitute(
                body, definition, raw_bindings, expanded_bindings, token.start, end
            )
            generated_replacement = []
            for value in replacement:
                self.budget.consume()
                if value.kind != 'empty':
                    generated_replacement.append(replace(
                        value, start=token.start, end=end,
                        hidden=value.hidden | hidden, generated=True,
                    ))
            if generated_replacement:
                pending.extendleft(reversed(generated_replacement))
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
        # Invocation recognition happens before the next token is expanded.
        # An intervening token breaks a pending call even if it expands away or
        # becomes '('. Whitespace/comment-only buffers do not break adjacency.
        if original and original[0].text != '(':
            self.trailing_function = None
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