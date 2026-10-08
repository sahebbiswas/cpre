"""Unknown macro dependencies of preprocessor conditions.

An *unknown* macro is a name whose external state (definedness or replacement
value) is not fixed by the current configuration and would have to be supplied
to make a conditional concrete. This module is independent of the CLI so other
tooling, such as configuration generators, can reuse it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from .api import AnalysisOptions, _translate_parse_error
from .configuration import MacroConfiguration, _condition_environment, _configured_environment
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, ExpansionError, Token, tokenize
from .macros import MacroDefinition, MacroEnvironment, _apply_macro_directive
from .model import ConditionalBranch, ConditionError
from .parser import DIRECTIVE_RE, logical_lines, parse_source
from .robdd import AnalysisBudget, AnalysisLimitExceeded

# Operators with standard semantics, and location-sensitive builtins whose values
# come from the source itself; none of these can be supplied as -D/-U.
_NOT_CONFIGURABLE = frozenset(
    {"defined", "__has_include", "__has_include_next", "__LINE__", "__FILE__", "__VA_ARGS__"}
)
_DEFINEDNESS_DIRECTIVES = frozenset({"ifdef", "ifndef", "elifdef", "elifndef"})
_MACRO_DIRECTIVE_RE = re.compile(r"^\s*#\s*(define|undef)\b(.*)$")


class MacroUse(str, Enum):
    """How a condition depends on a macro name."""

    DEFINEDNESS = "definedness"
    """The name appears only under ``defined``/``#ifdef``/``#ifndef``."""
    VALUE = "value"
    """The name is macro expanded and evaluated as an integer value."""


_USE_ORDER = (MacroUse.DEFINEDNESS, MacroUse.VALUE)


@dataclass(frozen=True)
class UnknownMacro:
    """A macro name whose external state a condition depends on.

    ``uses`` lists each distinct way the name is used, definedness first.
    ``locations`` are the conditional directive lines that depend on it, in
    source order.
    """

    name: str
    uses: tuple[MacroUse, ...]
    locations: tuple[SourceLocation, ...] = ()

    @property
    def suggestions(self) -> tuple[str, ...]:
        """Command-line forms that would fix the name's state.

        A definedness-only dependency never suggests a value: ``-D NAME`` or
        ``-U NAME`` already decides it. A value use needs replacement text.
        """
        if MacroUse.VALUE in self.uses:
            return (f"-D {self.name}=<value>", f"-U {self.name}")
        return (f"-D {self.name}", f"-U {self.name}")


def _collect(
    uses: Mapping[str, set[MacroUse]],
    lines: Mapping[str, set[int]] | None = None,
) -> tuple[UnknownMacro, ...]:
    return tuple(
        UnknownMacro(
            name,
            tuple(use for use in _USE_ORDER if use in uses[name]),
            tuple(SourceLocation(line) for line in sorted(lines.get(name, ()))) if lines else (),
        )
        for name in sorted(uses)
        if uses[name]
    )


def _significant(text: str) -> list[Token]:
    """Significant tokens, without the header operands of ``__has_include``."""
    tokens = [token for token in tokenize(text) if token.kind not in {"space", "comment"}]
    result: list[Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        result.append(token)
        index += 1
        if token.text in {"__has_include", "__has_include_next"} and (
            index < len(tokens) and tokens[index].text == "("
        ):
            depth = 0
            while index < len(tokens):
                depth += {"(": 1, ")": -1}.get(tokens[index].text, 0)
                index += 1
                if depth == 0:
                    break
    return result


def _defined_operand(tokens: list[Token], index: int) -> tuple[str, int] | None:
    """Return ``(name, next_index)`` for ``defined NAME`` or ``defined(NAME)``."""
    if index + 1 < len(tokens) and tokens[index + 1].kind == "identifier":
        return tokens[index + 1].text, index + 2
    if (
        index + 3 < len(tokens)
        and tokens[index + 1].text == "("
        and tokens[index + 2].kind == "identifier"
        and tokens[index + 3].text == ")"
    ):
        return tokens[index + 2].text, index + 4
    return None


def _unresolved_uses(
    text: str,
    environment: MacroEnvironment,
    *,
    definedness_directive: bool,
    max_work: int,
) -> dict[str, set[MacroUse]]:
    """Names that keep one condition from evaluating under ``environment``.

    ``environment`` is the exact macro state at the condition. Known
    definedness is substituted before macro expansion, as in concrete
    evaluation; identifiers that survive expansion without replacement text are
    value dependencies. Expansion uses its own budget so diagnostics never
    consume or exhaust the preprocessing run's budget.
    """
    uses: dict[str, set[MacroUse]] = {}
    if definedness_directive:
        if text not in _NOT_CONFIGURABLE and environment.get(text).defined is None:
            uses[text] = {MacroUse.DEFINEDNESS}
        return uses

    def substitute_defined(tokens: list[Token]) -> list[Token]:
        result: list[Token] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            operand = (
                _defined_operand(tokens, index)
                if token.kind == "identifier" and token.text == "defined"
                else None
            )
            if operand is None:
                result.append(token)
                index += 1
                continue
            name, index = operand
            defined = environment.get(name).defined
            if defined is None and name not in _NOT_CONFIGURABLE:
                uses.setdefault(name, set()).add(MacroUse.DEFINEDNESS)
            result.append(
                Token("1" if defined else "0", "number", token.start, token.end, generated=True)
            )
        return result

    tokens = substitute_defined(_significant(text))
    condition_environment = _condition_environment(environment)
    try:
        expanded = Expansion(text, AnalysisBudget(max_work))._expand(tokens, condition_environment)
        # A replacement list may itself spell ``defined``.
        expanded = substitute_defined([token for token in expanded if token.kind != "empty"])
    except (ExpansionError, AnalysisLimitExceeded):
        expanded = tokens
    for token in expanded:
        if token.kind != "identifier" or token.text in _NOT_CONFIGURABLE:
            continue
        if condition_environment.get(token.text).definition is None:
            uses.setdefault(token.text, set()).add(MacroUse.VALUE)
    return uses


def _unresolved_macros(
    text: str,
    environment: MacroEnvironment,
    *,
    directive: str,
    line: int,
    max_work: int,
) -> tuple[UnknownMacro, ...]:
    """Structured unresolved names for one undetermined preprocessing condition."""
    uses = _unresolved_uses(
        text,
        environment,
        definedness_directive=directive in _DEFINEDNESS_DIRECTIVES,
        max_work=max_work,
    )
    return _collect(uses, {name: {line} for name in uses})


def _base_environment(configuration: MacroConfiguration | None) -> MacroEnvironment:
    if configuration is None:
        return MacroEnvironment()
    return _configured_environment(configuration)


def unknown_macros(
    expression: str,
    *,
    configuration: MacroConfiguration | None = None,
    options: AnalysisOptions | None = None,
) -> tuple[UnknownMacro, ...]:
    """Return the macro names an ``#if`` expression needs to become concrete.

    ``expression`` is the text after ``#if``/``#elif``. Names already fixed by
    ``configuration`` are not unknown; configured definitions are expanded, so a
    name reached only through a configured macro's replacement is reported.
    Results are sorted by name. ``defined(NAME)`` contributes a
    :attr:`MacroUse.DEFINEDNESS` dependency; any other identifier that survives
    expansion contributes :attr:`MacroUse.VALUE`.
    """
    if not isinstance(expression, str):
        raise AnalysisError("expression must be a string", code=ErrorCode.INVALID_CONFIGURATION)
    resolved = options if options is not None else AnalysisOptions()
    uses = _unresolved_uses(
        expression,
        _base_environment(configuration),
        definedness_directive=False,
        max_work=resolved._resource_limits().max_work,
    )
    return _collect(uses)


class _SourceDependencies:
    """Source-order, path-insensitive sweep over every conditional directive."""

    def __init__(self, configuration: MacroConfiguration | None) -> None:
        self.external = _base_environment(configuration)
        # The enclosing conditional branches (by directive line) of the current line.
        self.path: list[int] = []
        # Branch paths of every #define/#undef seen so far, per name. A directive
        # whose path is a prefix of a later use's path executes before that use
        # on every path reaching it, so the external state no longer matters.
        self.assignments: dict[str, list[tuple[int, ...]]] = {}
        # Every definition the source may have made so far, on any path.
        self.definitions: dict[str, list[MacroDefinition]] = {}
        self.uses: dict[str, set[MacroUse]] = {}
        self.lines: dict[str, set[int]] = {}

    def _assigned(self, name: str) -> bool:
        path = tuple(self.path)
        return any(path[: len(assigned)] == assigned for assigned in self.assignments.get(name, ()))

    def _external_matters(self, name: str, use: MacroUse) -> bool:
        if name in _NOT_CONFIGURABLE or self._assigned(name):
            return False
        state = self.external.get(name)
        if use is MacroUse.DEFINEDNESS:
            return state.defined is None
        return state.defined is not False and state.definition is None

    def _record(self, name: str, use: MacroUse, line: int) -> None:
        if self._external_matters(name, use):
            self.uses.setdefault(name, set()).add(use)
            self.lines.setdefault(name, set()).add(line)

    def _candidates(self, name: str) -> Iterable[MacroDefinition]:
        yield from self.definitions.get(name, ())
        if not self._assigned(name):
            definition = self.external.get(name).definition
            if definition is not None:
                yield definition

    def condition(self, text: str, directive: str, line: int) -> None:
        if directive in _DEFINEDNESS_DIRECTIVES:
            self._record(text, MacroUse.DEFINEDNESS, line)
            return
        pending: list[tuple[list[Token], frozenset[str]]] = [(_significant(text), frozenset())]
        visited: set[str] = set()
        while pending:
            tokens, parameters = pending.pop()
            index = 0
            while index < len(tokens):
                token = tokens[index]
                index += 1
                if token.kind != "identifier" or token.text in parameters:
                    continue
                if token.text == "defined":
                    operand = _defined_operand(tokens, index - 1)
                    if operand is not None:
                        name, index = operand
                        self._record(name, MacroUse.DEFINEDNESS, line)
                    continue
                self._record(token.text, MacroUse.VALUE, line)
                if token.text in visited:
                    continue
                visited.add(token.text)
                for definition in self._candidates(token.text):
                    names = frozenset((*(definition.parameters or ()), "__VA_ARGS__"))
                    pending.append((_significant(definition.replacement), names))

    def conditional(self, kind: str, line: int) -> None:
        if kind in {"if", "ifdef", "ifndef"}:
            self.path.append(line)
        elif kind == "endif":
            if self.path:
                self.path.pop()
        elif self.path:
            self.path[-1] = line

    def directive(self, kind: str, remainder: str, line: int) -> None:
        scratch = MacroEnvironment()
        try:
            _apply_macro_directive(scratch, kind, remainder, SourceLocation(line))
        except ValueError:
            # Malformed directives are reported by preprocessing itself.
            return
        name_match = re.match(r"\s*([A-Za-z_]\w*)", remainder)
        assert name_match is not None
        name = name_match[1]
        definition = scratch.get(name).definition
        self.assignments.setdefault(name, []).append(tuple(self.path))
        if definition is not None:
            self.definitions.setdefault(name, []).append(definition)


def unknown_macros_in_source(
    source: str,
    *,
    filename: str | None = None,
    configuration: MacroConfiguration | None = None,
) -> tuple[UnknownMacro, ...]:
    """Return the macro names needed to make every conditional in ``source`` concrete.

    Every conditional directive is inspected, reachable or not, because
    reachability itself depends on the unknown names; the result is therefore a
    sound over-approximation suitable for seeding a configuration. A name is not
    unknown when ``configuration`` fixes the state a use needs, or when an earlier
    ``#define``/``#undef`` in the same or an enclosing conditional branch assigns
    it on every path reaching the use (so include-guarded bodies work as
    expected). Definitions made in other branches keep the name unknown, since the
    external state still matters on paths that skip them, and every candidate
    replacement list is followed for further dependencies. Included headers are
    not read.
    Results are sorted by name; ``locations`` lists the dependent directives.
    """
    if not isinstance(source, str):
        raise AnalysisError("source must be a string", code=ErrorCode.INVALID_CONFIGURATION)
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error
    branches: dict[int, ConditionalBranch] = {}
    pending = list(tree.groups)
    while pending:
        group = pending.pop()
        for branch in group.branches:
            branches[branch.line] = branch
            pending.extend(branch.children)

    sweep = _SourceDependencies(configuration)
    for line in logical_lines(source):
        conditional = DIRECTIVE_RE.match(line.text)
        if conditional is not None:
            # A condition is evaluated in the enclosing context, before its branch.
            found = branches.get(line.start_line)
            if found is not None and found.expression_text is not None:
                if conditional[1] in {"if", "ifdef", "ifndef"}:
                    sweep.condition(found.expression_text, found.directive, line.start_line)
                else:
                    # An #elif is reached only from the group's enclosing context.
                    sweep.path.pop()
                    sweep.condition(found.expression_text, found.directive, line.start_line)
                    sweep.path.append(line.start_line)
            sweep.conditional(conditional[1], line.start_line)
            continue
        macro = _MACRO_DIRECTIVE_RE.match(line.text)
        if macro is not None:
            sweep.directive(macro[1], macro[2], line.start_line)
    return _collect(sweep.uses, sweep.lines)


__all__ = ["UnknownMacro", "MacroUse", "unknown_macros", "unknown_macros_in_source"]
