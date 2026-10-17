"""Token-aware, bounded macro expansion and physical source mappings.

``Expansion`` is the facade over the expansion stages, each in its own module with
explicit inputs and outputs:

1. ``_replacement``: tokenize a replacement list and lift its ``__VA_OPT__`` groups;
2. ``_arguments``: collect an invocation's arguments and bind them to parameters;
3. ``_substitution``: apply ``#``, ``##``, ``__VA_OPT__`` and parameter replacement;
4. ``_rescan``: prescan arguments and rescan the result against the macro environment;
5. ``_expansion_output``: merge the generated tokens into edits and render source maps.

This module keeps the buffering of source lines between directives and the adjacency
rules for invocations that straddle lines.
"""

from __future__ import annotations

from bisect import bisect_right

from ._expansion_output import Edit, SourceLocator, SourceMapping, merged_edits, render
from ._replacement import ExpansionError, ReplacementCache
from ._rescan import Rescanner
from ._substitution import Substituter
from .errors import SourceLocation
from .lexer import Token, tokenize
from .macros import MacroDefinition, MacroLookup
from .robdd import AnalysisBudget, AnalysisLimitExceeded

__all__ = ["Expansion", "ExpansionError", "SourceMapping", "tokenize"]


class Expansion:
    def __init__(self, source: str, budget: AnalysisBudget):
        self.source = source
        self.budget = budget
        self.tokens = tokenize(source)
        self.starts = [token.start for token in self.tokens]
        self.trailing_function: str | None = None
        self.pending_start: int | None = None
        self.pending_end = 0
        self.locator = SourceLocator(source)
        self.edits: list[Edit] = []
        self.replacements = ReplacementCache()
        self.rescanner = Rescanner(budget, Substituter(), self.replacements)

    @property
    def current_offset(self) -> int:
        """Offset of the token being expanded, for locating budget and error reports."""
        return self.rescanner.current_offset

    @current_offset.setter
    def current_offset(self, offset: int) -> None:
        self.rescanner.current_offset = offset

    def location(self, offset: int) -> SourceLocation:
        return self.locator.location(offset)

    def directive_text(self, start: int, end: int) -> str:
        first = bisect_right(self.starts, start - 1)
        last = bisect_right(self.starts, end - 1)
        return "".join(
            " " if token.kind == "comment" else token.text for token in self.tokens[first:last]
        ).strip()

    def validate_definition(self, definition: MacroDefinition) -> None:
        """Validate replacement-list constructs whose legality is definition-local."""
        self.replacements.validate(definition)

    def _expand(self, tokens: list[Token], environment: MacroLookup) -> list[Token]:
        return self.rescanner.expand(tokens, environment)

    def line(self, start: int, end: int) -> None:
        # Buffer adjacent ordinary lines so invocations may span physical lines.
        # The caller flushes before directives mutate the macro environment.
        if self.pending_start is None:
            self.pending_start = start
        self.pending_end = end

    def flush(self, environment: MacroLookup) -> None:
        if self.pending_start is None:
            return
        first = bisect_right(self.starts, self.pending_start - 1)
        last = bisect_right(self.starts, self.pending_end - 1)
        original = [t for t in self.tokens[first:last] if t.kind not in {"space", "comment"}]
        self.pending_start = None
        self._reject_reserved(original)
        try:
            expanded = self._expand(original, environment)
        except AnalysisLimitExceeded as error:
            error.line = self.location(self.current_offset).line
            raise
        self._track_adjacency(original, expanded, environment)
        self.edits.extend(merged_edits(self.source, expanded))

    def _reject_reserved(self, original: list[Token]) -> None:
        """``__VA_OPT__`` is only meaningful inside a variadic replacement list."""
        for token in original:
            if token.kind == "identifier" and token.text == "__VA_OPT__":
                self.current_offset = token.start
                raise ExpansionError(
                    "__VA_OPT__ is only valid in a variadic macro replacement list"
                )

    def _track_adjacency(
        self, original: list[Token], expanded: list[Token], environment: MacroLookup
    ) -> None:
        """Remember a function-like macro name ending the buffer, and reject a call that
        its next buffer would complete across a directive."""
        # Invocation recognition happens before the next token is expanded.
        # An intervening token breaks a pending call even if it expands away or
        # becomes '('. Whitespace/comment-only buffers do not break adjacency.
        if original and original[0].text != "(":
            self.trailing_function = None
        significant = [t for t in expanded if t.kind != "empty"]
        if not significant:
            return
        if self.trailing_function is not None and significant[0].text == "(":
            self.current_offset = significant[0].start
            raise ExpansionError(
                "macro invocation crossing a preprocessing directive is unsupported"
            )
        final = significant[-1]
        definition = environment.get(final.text).definition if final.kind == "identifier" else None
        self.trailing_function = (
            final.text
            if definition is not None
            and definition.parameters is not None
            and final.text not in final.hidden
            else None
        )

    def render(self, masked: str) -> tuple[str, tuple[SourceMapping, ...]]:
        return render(masked, self.edits, self.locator)
