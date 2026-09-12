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
_VA_OPT_KIND = 'va_opt'


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
        self.cache: dict[
            tuple[str, tuple[str, ...] | None, bool],
            tuple[list[Token], dict[str, list[Token]]],
        ] = {}

    def location(self, offset: int) -> SourceLocation:
        index = bisect_right(self.line_starts, offset) - 1
        return SourceLocation(index + 1, offset - self.line_starts[index] + 1)

    def directive_text(self, start: int, end: int) -> str:
        first = bisect_right(self.starts, start - 1)
        last = bisect_right(self.starts, end - 1)
        return ''.join(' ' if token.kind == 'comment' else token.text
                       for token in self.tokens[first:last]).strip()

    def validate_definition(self, definition: MacroDefinition) -> None:
        """Validate replacement-list constructs whose legality is definition-local."""
        if definition.name == '__VA_OPT__':
            raise ExpansionError('__VA_OPT__ cannot be used as a macro name')
        if definition.parameters is not None and '__VA_OPT__' in definition.parameters:
            raise ExpansionError(
                f'__VA_OPT__ cannot be a named parameter: {definition.name}'
            )
        self._replacement(definition)

    def _validate_va_opt_content(
        self, content: list[Token], definition: MacroDefinition
    ) -> None:
        """Validate #/## placement inside a va-opt replacement list."""
        if not content:
            return
        if self._paste_operator(content[0]) or self._paste_operator(content[-1]):
            raise ExpansionError(
                f'token paste operator cannot appear at an edge of __VA_OPT__ in {definition.name}'
            )
        parameters = set(definition.parameters or ()) | {'__VA_ARGS__'}
        for index, part in enumerate(content):
            if self._paste_operator(part):
                if index + 1 >= len(content) or self._paste_operator(content[index + 1]):
                    raise ExpansionError(
                        f'invalid token paste placement in __VA_OPT__ of {definition.name}'
                    )
                continue
            if self._stringify_operator(part):
                if index + 1 >= len(content):
                    raise ExpansionError(
                        f'stringification operator in __VA_OPT__ of {definition.name} '
                        'is not followed by a parameter'
                    )
                operand = content[index + 1]
                if operand.kind != 'identifier' or operand.text not in parameters:
                    raise ExpansionError(
                        f'stringification operator in __VA_OPT__ of {definition.name} '
                        'is not followed by a parameter'
                    )

    def _replacement(
        self, definition: MacroDefinition
    ) -> tuple[list[Token], dict[str, list[Token]]]:
        key = (definition.replacement, definition.parameters, definition.variadic)
        if key in self.cache:
            return self.cache[key]

        tokens = [t for t in tokenize(definition.replacement)
                  if t.kind not in {'space', 'comment'}]
        body: list[Token] = []
        va_opts: dict[str, list[Token]] = {}
        index = 0
        occurrence = 0
        while index < len(tokens):
            token = tokens[index]
            if token.kind != 'identifier' or token.text != '__VA_OPT__':
                body.append(token)
                index += 1
                continue
            if definition.parameters is None or not definition.variadic:
                raise ExpansionError(
                    f'__VA_OPT__ requires a variadic function-like macro: {definition.name}'
                )
            if index + 1 >= len(tokens) or tokens[index + 1].text != '(':
                raise ExpansionError(
                    f'__VA_OPT__ in {definition.name} must be followed by a parenthesized token sequence'
                )

            depth = 0
            cursor = index + 2
            content: list[Token] = []
            while cursor < len(tokens):
                current = tokens[cursor]
                if current.kind == 'identifier' and current.text == '__VA_OPT__':
                    raise ExpansionError(
                        f'nested __VA_OPT__ is not allowed in {definition.name}'
                    )
                if current.text == '(':
                    depth += 1
                elif current.text == ')':
                    if depth == 0:
                        break
                    depth -= 1
                content.append(current)
                cursor += 1
            if cursor >= len(tokens):
                raise ExpansionError(f'unterminated __VA_OPT__ in {definition.name}')

            self._validate_va_opt_content(content, definition)
            binding = f'\x00va_opt_{occurrence}'
            body.append(Token(binding, _VA_OPT_KIND, token.start, tokens[cursor].end))
            va_opts[binding] = content
            occurrence += 1
            index = cursor + 1

        self.cache[key] = (body, va_opts)
        return body, va_opts

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
            if token.kind == 'empty':
                continue
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
        left = [token for token in left if token.kind != 'empty']
        right = [token for token in right if token.kind != 'empty']
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
                    start: int, end: int,
                    va_opts: dict[str, list[Token]] | None = None) -> list[Token]:
        """Apply #/## and parameter substitution before the replacement rescan."""
        if va_opts:
            va_args = expanded.get('__VA_ARGS__', ())
            active = any(token.kind != 'empty' for token in va_args)
            raw = dict(raw)
            expanded = dict(expanded)
            for binding, content in va_opts.items():
                replacement = (
                    self._substitute(content, definition, raw, expanded, start, end)
                    if active else []
                )
                raw[binding] = replacement
                expanded[binding] = replacement

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
                if operand.kind not in {'identifier', _VA_OPT_KIND} or operand.text not in parameters:
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
            if part.kind in {'identifier', _VA_OPT_KIND} and part.text in parameters:
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

    def _parameter_needs_expansion(self, parameter: str, body: list[Token]) -> bool:
        for index, part in enumerate(body):
            if part.kind != 'identifier' or part.text != parameter:
                continue
            if index > 0 and self._stringify_operator(body[index - 1]):
                continue
            if index > 0 and self._paste_operator(body[index - 1]):
                continue
            if index + 1 < len(body) and self._paste_operator(body[index + 1]):
                continue
            return True
        return False

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

            body, va_opts = self._replacement(definition)
            end = token.end
            hidden = token.hidden | {token.text}
            raw_bindings: dict[str, list[Token]] = {}
            expanded_bindings: dict[str, list[Token]] = {}
            va_opt_active = False
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

                # __VA_OPT__ tests the ordinary, fully expanded substitution of
                # __VA_ARGS__, even when __VA_ARGS__ is otherwise unused or only
                # appears next to #/##.
                if va_opts:
                    argument = raw_bindings['__VA_ARGS__']
                    expanded_bindings['__VA_ARGS__'] = (yield argument)
                    va_opt_active = any(
                        part.kind != 'empty'
                        for part in expanded_bindings['__VA_ARGS__']
                    )

                for parameter, argument in raw_bindings.items():
                    if parameter in expanded_bindings:
                        continue
                    needs_expansion = self._parameter_needs_expansion(parameter, body)
                    if va_opt_active and not needs_expansion:
                        needs_expansion = any(
                            self._parameter_needs_expansion(parameter, content)
                            for content in va_opts.values()
                        )
                    expanded_bindings[parameter] = (yield argument) if needs_expansion else argument
                self.current_offset = token.start

            replacement = self._substitute(
                body, definition, raw_bindings, expanded_bindings, token.start, end, va_opts
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
