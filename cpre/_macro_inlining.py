"""Inlining of referenced Boolean macros for macro simplification (``expand_macros``).

Macro simplification normally treats every identifier in a replacement list as an
opaque atom. With expansion enabled, an identifier naming another macro defined in
the same source is replaced by that macro's own (parsed) replacement, so the reduction
sees through ``#define D (A && C)`` when ``A`` is ``(B || C)``.

Only substitutions that are sound for every build configuration are made:

* the macro must be a Boolean candidate (or a bare identifier alias of one), never a
  plain constant or any other non-candidate, because such a value is configuration
  dependent and a deliberate ``0``/``1`` switch must stay visible;
* its replacement must be substitution-safe: a top-level ``&&`` or ``||`` outside
  parentheses binds with the operators around each use (``#define A B || C`` makes
  ``A && E`` mean ``B || (C && E)``), so such a macro is not expanded as a unit;
* C binds a macro body where it is used, not where it is defined, so the referenced
  macro must have exactly one ``#define`` and no ``#undef`` in the source, precede the
  macro that uses it, and sit in a conditional block that also encloses that macro;
* definition cycles are left as atoms.

Names that are not substituted stay atoms. The reason is recorded so the report can
say why.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .lexer import CONDITIONAL_DIRECTIVES, LogicalLine, macro_name
from .macros import MacroDefinition
from .model import (
    Conjunction,
    Disjunction,
    Expression,
    Negation,
    Variable,
)
from .robdd import AnalysisLimitExceeded

# One entry per open conditional group: (group number, branch number). A macro defined
# in a conditional context is only guaranteed to exist where that whole stack is open.
ConditionalContext = tuple[tuple[int, int], ...]

_OPENERS = frozenset({"if", "ifdef", "ifndef"})
_BRANCHES = frozenset({"elif", "elifdef", "elifndef", "else"})


@dataclass(frozen=True)
class DefineSite:
    """One ``#define`` of a name: its text, position and conditional context."""

    definition: MacroDefinition
    order: int
    context: ConditionalContext


@dataclass
class MacroScope:
    """Every ``#define`` and ``#undef`` of each name in one source."""

    defines: dict[str, list[DefineSite]] = field(default_factory=dict)
    undefined: set[str] = field(default_factory=set)

    def add_define(self, site: DefineSite) -> None:
        self.defines.setdefault(site.definition.name, []).append(site)


class ConditionalTracker:
    """Follows ``#if`` / ``#elif`` / ``#else`` / ``#endif`` nesting line by line."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, int]] = []
        self._groups = 0

    @property
    def context(self) -> ConditionalContext:
        return tuple(self._stack)

    def feed(self, line: LogicalLine) -> None:
        directive = line.directive
        if directive is None or directive.name not in CONDITIONAL_DIRECTIVES:
            return
        name = directive.name
        if name in _OPENERS:
            self._groups += 1
            self._stack.append((self._groups, 0))
        elif name in _BRANCHES:
            if self._stack:
                group, branch = self._stack[-1]
                self._stack[-1] = (group, branch + 1)
        elif name == "endif" and self._stack:
            self._stack.pop()


def undefined_name(line: LogicalLine) -> str | None:
    """The macro named by an ``#undef`` line, or ``None`` for any other line."""
    directive = line.directive
    if directive is None or directive.name != "undef":
        return None
    return macro_name(line.text[directive.name_end :])


@dataclass(frozen=True)
class Inlined:
    """The outcome of expanding one expression."""

    expression: Expression
    expanded: tuple[str, ...]
    notes: tuple[str, ...]


Parser = Callable[[MacroDefinition], Expression | str | None]
"""Gives the expression to substitute for a definition, a reason it must not be substituted,
or ``None`` when it is simply not a macro that is expanded (a plain constant, say)."""

Expander = Callable[[Expression, int], Inlined]
"""Expands an expression; the second argument is the node budget (``max_work``)."""


# Expansion nested deeper than this many macros is left as atoms rather than recursed into.
MAX_NESTING = 128

# Deepest expression (operators and parentheses, counted through expanded bodies) that
# expansion builds. Each body is accepted on its own, but a chain of them multiplies the
# depth, and the recursive passes after expansion must stay well inside Python's stack.
MAX_DEPTH = 256


class _NodeBudget:
    """Counts the nodes expansion creates and stops it before it grows without bound."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def enter(self, depth: int) -> None:
        """Stop an expression nested deeper than :data:`MAX_DEPTH`."""
        if depth > MAX_DEPTH:
            raise AnalysisLimitExceeded("nesting", MAX_DEPTH, depth)

    def take(self) -> None:
        self.count += 1
        if self.count > self.limit:
            raise AnalysisLimitExceeded("work", self.limit, self.count)


class Inliner:
    """Substitutes referenced Boolean macros, honouring the soundness rules above."""

    def __init__(
        self,
        scope: MacroScope,
        parse: Parser,
    ) -> None:
        self._scope = scope
        self._parse = parse
        self._parsed: dict[str, Expression | str | None] = {}

    @classmethod
    def from_definitions(
        cls,
        definitions: Mapping[str, MacroDefinition],
        parse: Parser,
    ) -> Inliner:
        """An inliner over caller-supplied definitions.

        Each definition is taken to be the single, unconditional definition of its name
        that precedes every macro analyzed against it.
        """
        scope = MacroScope()
        for name, definition in definitions.items():
            if definition.name != name:
                raise ValueError(f"definition for {name!r} names {definition.name!r}")
            scope.add_define(DefineSite(definition, -1, ()))
        return cls(scope, parse)

    def bind(self, user: str, order: int, context: ConditionalContext) -> Expander:
        """An expander for the macro ``user`` defined at ``order`` in ``context``."""

        def expand(expression: Expression, max_size: int) -> Inlined:
            expanded: list[str] = []
            notes: list[str] = []
            budget = _NodeBudget(max_size)
            result = self._walk(
                expression, user, order, context, (user,), expanded, notes, budget, 0
            )
            return Inlined(result, tuple(dict.fromkeys(expanded)), tuple(dict.fromkeys(notes)))

        return expand

    def _walk(
        self,
        expression: Expression,
        user: str,
        order: int,
        context: ConditionalContext,
        stack: tuple[str, ...],
        expanded: list[str],
        notes: list[str],
        budget: _NodeBudget,
        depth: int,
    ) -> Expression:
        budget.take()
        budget.enter(depth)
        if isinstance(expression, Negation):
            operand = self._walk(
                expression.operand, user, order, context, stack, expanded, notes, budget, depth + 1
            )
            return Negation(operand)
        if isinstance(expression, (Conjunction, Disjunction)):
            return type(expression)(
                tuple(
                    self._walk(
                        operand, user, order, context, stack, expanded, notes, budget, depth + 1
                    )
                    for operand in expression.operands
                )
            )
        if type(expression) is not Variable:
            # Constants, symbolic literals, definedness atoms and predicates are not macros.
            return expression
        name = expression.name
        body = self._body(name, user, order, context, stack, notes)
        if body is None:
            return expression
        if name not in expanded:
            expanded.append(name)
        return self._walk(
            body, user, order, context, (*stack, name), expanded, notes, budget, depth + 1
        )

    def _body(
        self,
        name: str,
        user: str,
        order: int,
        context: ConditionalContext,
        stack: tuple[str, ...],
        notes: list[str],
    ) -> Expression | None:
        sites = self._scope.defines.get(name)
        if not sites:
            return None
        if name in stack:
            notes.append(f"{name} is part of a definition cycle")
            return None
        if len(stack) > MAX_NESTING:
            notes.append(f"{name} is nested more than {MAX_NESTING} macros deep")
            return None
        problem = self._problem(name, sites, user, order, context)
        if problem is not None:
            notes.append(problem)
            return None
        body = self._parse_once(sites[0].definition)
        if isinstance(body, str):
            notes.append(body)
            return None
        return body

    def _problem(
        self,
        name: str,
        sites: list[DefineSite],
        user: str,
        order: int,
        context: ConditionalContext,
    ) -> str | None:
        """Why ``name`` cannot be substituted into ``user``, or ``None`` when it can."""
        if name in self._scope.undefined:
            return f"{name} is #undef'd in the source"
        if len(sites) > 1:
            return f"{name} is defined more than once"
        site = sites[0]
        if site.order >= order:
            return f"{name} is defined after {user}"
        if context[: len(site.context)] != site.context:
            return f"{name} is defined in a conditional block that does not enclose {user}"
        return None

    def _parse_once(self, definition: MacroDefinition) -> Expression | str | None:
        key = definition.name
        if key not in self._parsed:
            self._parsed[key] = self._parse(definition)
        return self._parsed[key]
