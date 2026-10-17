"""Rescan: the final stage of macro expansion.

Walks a token stream, and for each invocation collects and prescans its arguments
(``_arguments``), substitutes them into the replacement list (``_substitution``) and
pushes the result back in front of the remaining tokens. Argument prescans are yielded
to an explicit trampoline in :meth:`Rescanner.expand`, never Python recursion, so deeply
nested invocations cannot exhaust the interpreter stack.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Generator
from dataclasses import replace
from typing import NamedTuple

from ._arguments import bind_arguments, collect_arguments
from ._replacement import (
    ExpansionError,
    Replacement,
    ReplacementCache,
    is_paste_operator,
    is_stringify_operator,
)
from ._substitution import Bindings, Substituter
from .lexer import Token
from .macros import MacroDefinition, MacroLookup
from .robdd import AnalysisBudget

# A rescan generator yields token lists that need their own full prescan and is sent the
# expanded result; it finishes by returning its own expanded tokens.
Rescan = Generator[list[Token], list[Token], list[Token]]


class _Invocation(NamedTuple):
    """One macro invocation after argument collection."""

    end: int
    hidden: frozenset[str]
    raw: Bindings
    expanded: Bindings


class Rescanner:
    """Expands token streams against a macro environment within a work budget."""

    def __init__(
        self, budget: AnalysisBudget, substituter: Substituter, replacements: ReplacementCache
    ):
        self.budget = budget
        self.substituter = substituter
        self.replacements = replacements
        # Offset of the token being expanded, for locating budget and error reports.
        self.current_offset = 0

    def expand(self, tokens: list[Token], environment: MacroLookup) -> list[Token]:
        """Fully expand ``tokens``, running argument prescans on an explicit stack."""
        stack = [self._rescan(tokens, environment)]
        reply: list[Token] | None = None
        result: list[Token] = []
        while stack:
            top = stack[-1]
            try:
                request = next(top) if reply is None else top.send(reply)
            except StopIteration as finished:
                stack.pop()
                result = reply = finished.value
            else:
                stack.append(self._rescan(request, environment))
                reply = None
        return result

    def _definition_for(
        self, token: Token, pending: deque[Token], environment: MacroLookup
    ) -> MacroDefinition | None:
        """The definition ``token`` invokes here, or ``None`` if it is not an invocation."""
        if token.kind != "identifier":
            return None
        state = environment.get(token.text)
        definition = state.definition
        if (
            definition is None
            and state.defined is not False
            and (state.defined is True or state.value is not None)
        ):
            raise ExpansionError(f"replacement text for assumed macro {token.text} is unknown")
        if definition is None:
            return None
        if definition.parameters is None or (pending and pending[0].text == "("):
            return definition
        return None

    def _rescan(self, tokens: list[Token], environment: MacroLookup) -> Rescan:
        pending = deque(tokens)
        emitted: list[Token] = []
        while pending:
            token = pending.popleft()
            self.current_offset = token.start
            self.budget.consume()
            if token.kind == "identifier" and token.text == "__VA_OPT__":
                raise ExpansionError(
                    "__VA_OPT__ is only valid in a variadic macro replacement list"
                )
            definition = self._definition_for(token, pending, environment)
            if definition is None or token.text in token.hidden:
                if token.generated:
                    self.budget.work += max(0, len(token.text) - 1)
                    self.budget.consume()
                emitted.append(token)
                continue

            replacement = self.replacements.get(definition)
            invocation = yield from self._invoke(token, definition, replacement, pending)
            generated = self._generate(token, definition, replacement, invocation)
            if generated:
                pending.extendleft(reversed(generated))
            else:
                emitted.append(
                    Token("", "empty", token.start, invocation.end, invocation.hidden, True)
                )
        return emitted

    def _invoke(
        self,
        token: Token,
        definition: MacroDefinition,
        replacement: Replacement,
        pending: deque[Token],
    ) -> Generator[list[Token], list[Token], _Invocation]:
        """Collect the arguments of ``token`` and prescan those the body expands."""
        hidden = token.hidden | {token.text}
        if definition.parameters is None:
            return _Invocation(token.end, hidden, {}, {})
        arguments, separators, closing = collect_arguments(pending, definition.name, self.budget)
        raw = bind_arguments(definition, arguments, separators)
        expanded = yield from self._prescan(raw, replacement)
        self.current_offset = token.start
        return _Invocation(
            max(token.end, closing.end),
            (token.hidden & closing.hidden) | {token.text},
            raw,
            expanded,
        )

    def _prescan(
        self, raw: Bindings, replacement: Replacement
    ) -> Generator[list[Token], list[Token], Bindings]:
        """Prescan each argument the replacement list uses outside ``#`` and ``##``."""
        body, va_opts = replacement.body, replacement.va_opts
        expanded: Bindings = {}
        va_opt_active = False
        # __VA_OPT__ tests the ordinary, fully expanded substitution of
        # __VA_ARGS__, even when __VA_ARGS__ is otherwise unused or only
        # appears next to #/##.
        if va_opts:
            expanded["__VA_ARGS__"] = yield raw["__VA_ARGS__"]
            va_opt_active = any(part.kind != "empty" for part in expanded["__VA_ARGS__"])

        for parameter, argument in raw.items():
            if parameter in expanded:
                continue
            needs_expansion = self._needs_expansion(parameter, body)
            if va_opt_active and not needs_expansion:
                needs_expansion = any(
                    self._needs_expansion(parameter, content) for content in va_opts.values()
                )
            expanded[parameter] = (yield argument) if needs_expansion else argument
        return expanded

    @staticmethod
    def _needs_expansion(parameter: str, body: list[Token]) -> bool:
        """Whether ``parameter`` appears in ``body`` outside ``#`` and ``##`` operands."""
        for index, part in enumerate(body):
            if part.kind != "identifier" or part.text != parameter:
                continue
            if index > 0 and is_stringify_operator(body[index - 1]):
                continue
            if index > 0 and is_paste_operator(body[index - 1]):
                continue
            if index + 1 < len(body) and is_paste_operator(body[index + 1]):
                continue
            return True
        return False

    def _generate(
        self,
        token: Token,
        definition: MacroDefinition,
        replacement: Replacement,
        invocation: _Invocation,
    ) -> list[Token]:
        """Substitute the arguments and mark the result as generated by this invocation."""
        substituted = self.substituter.substitute(
            replacement.body,
            definition,
            invocation.raw,
            invocation.expanded,
            token.start,
            invocation.end,
            replacement.va_opts,
        )
        generated = []
        for value in substituted:
            self.budget.consume()
            if value.kind != "empty":
                generated.append(
                    replace(
                        value,
                        start=token.start,
                        end=invocation.end,
                        hidden=value.hidden | invocation.hidden,
                        generated=True,
                    )
                )
        return generated
