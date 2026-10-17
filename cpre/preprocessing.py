"""Concrete conditional selection and macro expansion."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from . import _has_include
from ._lines import split_lines
from ._pragma_syntax import _mask_source_pragmas, _output_offset_for_source, _SourcePragma
from ._types import (
    AnalysisIncomplete,
    AnalysisOptions,
    MacroAssumptions,
    _normalize_assumptions,
    _translate_parse_error,
)
from .configuration import (
    MacroConfiguration,
    _condition_environment,
    _configured_environment,
    _ConfiguredMacroEnvironment,
    _detect_include_guard,
)
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, ExpansionError, SourceMapping
from .expressions import conjunction, disjunction, expression_atoms_in_order, negate
from .includes import (
    DEFAULT_MAX_INCLUDE_DEPTH,
    IncludeForm,
    IncludeOutcome,
    IncludeRecord,
    IncludeRequest,
    IncludeResolver,
    ResolvedInclude,
)
from .lexer import (
    HORIZONTAL_WHITESPACE,
    LogicalLine,
    Token,
    directive_head,
    logical_lines,
    macro_name,
    splice,
    tokenize,
)
from .macros import (
    MacroDefinition,
    MacroEnvironment,
    MacroLookup,
    MacroState,
    _apply_macro_directive,
)
from .model import (
    TRUE,
    ConditionalBranch,
    ConditionalGroup,
    ConditionalTree,
    ConditionError,
    DefinedVariable,
    Expression,
    Variable,
)
from .numeric_conditions import NumericConditionError, evaluate_numeric_condition
from .parser import parse_source
from .robdd import BDD, AnalysisBudget, AnalysisLimitExceeded, ResourceLimits
from .unknown_macros import UnknownMacro, _unresolved_macros

# These names have implementation-provided semantics in common C/C++ preprocessors.
# cpre must never silently certify them as ordinary identifiers. Explicit concrete
# definitions may model environment macros such as __STDC__; otherwise a reachable
# value use is reported as unsupported until cpre implements deterministic semantics.
_PREDEFINED_MACROS = frozenset(
    {
        "__BASE_FILE__",
        "__COUNTER__",
        "__DATE__",
        "__FILE__",
        "__INCLUDE_LEVEL__",
        "__LINE__",
        "__STDC__",
        "__STDC_HOSTED__",
        "__STDC_IEC_559__",
        "__STDC_IEC_559_COMPLEX__",
        "__STDC_ISO_10646__",
        "__STDC_LIB_EXT1__",
        "__STDC_MB_MIGHT_NEQ_WC__",
        "__STDC_NO_ATOMICS__",
        "__STDC_NO_COMPLEX__",
        "__STDC_NO_THREADS__",
        "__STDC_NO_VLA__",
        "__STDC_VERSION__",
        "__TIME__",
        "__TIMESTAMP__",
        "__cplusplus",
    }
)
_STANDARD_CONFIGURABLE_PREDEFINED = frozenset(
    {
        "__DATE__",
        "__STDC__",
        "__STDC_HOSTED__",
        "__STDC_IEC_559__",
        "__STDC_IEC_559_COMPLEX__",
        "__STDC_ISO_10646__",
        "__STDC_LIB_EXT1__",
        "__STDC_MB_MIGHT_NEQ_WC__",
        "__STDC_NO_ATOMICS__",
        "__STDC_NO_COMPLEX__",
        "__STDC_NO_THREADS__",
        "__STDC_NO_VLA__",
        "__STDC_VERSION__",
        "__TIME__",
        "__cplusplus",
    }
)
_DEFINEDNESS_DIRECTIVES = frozenset({"ifdef", "ifndef", "elifdef", "elifndef"})
# Non-conditional directives the core preprocessor acts on itself.
_CORE_DIRECTIVES = frozenset(
    ("endif", "define", "undef", "line", "include", "include_next", "import")
)


@dataclass(frozen=True, init=False)
class PreprocessingContext:
    """Deterministic standard predefined-macro values for one preprocessing run.

    ``standard_macros`` maps supported standard predefined names to exact
    preprocessing replacement text. Location-sensitive ``__LINE__`` and ``__FILE__``
    are intentionally not configurable here: they come from the active logical
    source location and ``filename``/``#line`` state. Build-time and language-mode
    values are never inferred from the host process.
    """

    standard_macros: Mapping[str, str]

    def __init__(self, *, standard_macros: Mapping[str, str] | None = None) -> None:
        supplied = dict(standard_macros or {})
        normalized: dict[str, str] = {}
        for name, replacement in supplied.items():
            if name not in _STANDARD_CONFIGURABLE_PREDEFINED:
                raise AnalysisError(
                    f"unsupported standard predefined macro in preprocessing context: {name!r}",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            if not isinstance(replacement, str) or not replacement.strip():
                raise AnalysisError(
                    f"replacement text for {name} must be a non-empty string",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            normalized[name] = replacement
        object.__setattr__(
            self,
            "standard_macros",
            MappingProxyType(dict(sorted(normalized.items()))),
        )


@dataclass(frozen=True)
class PreprocessDiagnostic:
    """A reachable construct that prevents supported concrete preprocessing.

    ``source_identity`` is ``None`` for the primary source and otherwise names the
    resolved included source that ``location`` refers to.

    For ``UNRESOLVED_CONDITION``, ``condition`` is the directive's expression as
    written (the macro name for ``#ifdef``-style directives) and ``unresolved``
    lists, sorted by name, the macros whose unknown state keeps it undetermined
    under the macro state at that point.
    """

    code: ErrorCode
    message: str
    location: SourceLocation
    source_identity: str | None = None
    condition: str | None = None
    unresolved: tuple[UnknownMacro, ...] = ()


@dataclass(frozen=True)
class SkippedInclude:
    """An active include directive masked by opt-in ``skip_includes`` preprocessing.

    ``directive`` is ``"include"``, ``"include_next"``, or ``"import"``.
    ``operand`` is the directive operand as written after physical line splicing
    and comment removal; it is not macro expanded or resolved. The skipped header's
    contents, including any macros or declarations it would supply, are unknown.
    ``source_identity`` is ``None`` for the primary source and otherwise names the
    resolved included source containing the directive.
    """

    directive: str
    operand: str
    location: SourceLocation
    source_identity: str | None = None


@dataclass(frozen=True)
class _IncludedPragma:
    """A reachable ``#pragma`` marker from an included source, in output coordinates."""

    payload: str
    location: SourceLocation
    source_identity: str
    output_start: int
    line_count: int


@dataclass(frozen=True)
class PreprocessResult:
    """Atomic selection result; incomplete results never expose partial source."""

    source: str | None
    filename: str | None = None
    incomplete: tuple[PreprocessDiagnostic | AnalysisIncomplete, ...] = ()

    macros: Mapping[str, MacroState] | None = None
    source_map: tuple[SourceMapping, ...] | None = None
    removed_lines: frozenset[int] | None = None
    skipped_includes: tuple[SkippedInclude, ...] = ()
    includes: tuple[IncludeRecord, ...] = ()
    _included_pragmas: tuple[_IncludedPragma, ...] = field(default=(), repr=False, compare=False)

    @property
    def complete(self) -> bool:
        return self.source is not None and not self.incomplete


# Branch-coverage verification hook: when set, the primary source records, for every
# conditional group it reaches, the line of the selected branch (None if none was).
_SELECTED_BRANCHES: ContextVar[dict[int, int | None] | None] = ContextVar(
    "_SELECTED_BRANCHES", default=None
)


@dataclass
class _IncludeState:
    """Include bookkeeping shared by every source in one preprocessing run."""

    resolver: IncludeResolver
    max_depth: int
    stack: list[str]
    once: set[str] = field(default_factory=set)
    imported: set[str] = field(default_factory=set)
    entered: set[str] = field(default_factory=set)
    guards: dict[str, str | None] = field(default_factory=dict)
    records: list[IncludeRecord] = field(default_factory=list)


@dataclass
class _Run:
    """State shared by the primary source and every included source."""

    base_environment: MacroEnvironment
    limits: ResourceLimits
    budget: AnalysisBudget
    skip_includes: bool
    includes: _IncludeState | None
    skipped: list[SkippedInclude]
    # Only the pragma layer dispatches pragma markers; other callers keep included
    # pragmas as unsupported directives rather than emitting inert markers.
    dispatch_pragmas: bool = False
    include_query: _has_include.IncludeQueryProvider | None = None
    # Answer __has_include queries the callback leaves open from the include resolver.
    has_include_from_resolver: bool = False
    # Macro names a seed source may not #define or #undef because the preprocessing context
    # supplies them (``MacroConfiguration.from_source``); None outside seed evaluation. A
    # configuration naming them could never be used with that context, so the seed is
    # rejected at the directive instead.
    protected_names: frozenset[str] | None = None


@dataclass(frozen=True)
class _UnitOutput:
    """Diagnostics, or canonical output of one source with its includes spliced in."""

    diagnostics: tuple[PreprocessDiagnostic | AnalysisIncomplete, ...]
    source: str = ""
    source_map: tuple[SourceMapping, ...] = ()
    removed_lines: frozenset[int] = frozenset()
    pragmas: tuple[_IncludedPragma, ...] = ()


@dataclass(frozen=True)
class _LogicalPreprocessingState:
    line: int
    file_literal: str | None


class _PredefinedMacroEnvironment:
    """Read-through macro environment with logical location-sensitive builtins."""

    def __init__(
        self,
        base: MacroEnvironment,
        expansion: Expansion,
        logical_states: dict[int, _LogicalPreprocessingState],
    ) -> None:
        self._base = base
        self._expansion = expansion
        self._logical_states = logical_states
        self._explicit_names = set(base.snapshot())
        self.logical_override: _LogicalPreprocessingState | None = None

    def _logical_state(self) -> _LogicalPreprocessingState | None:
        if self.logical_override is not None:
            return self.logical_override
        physical_line = self._expansion.location(self._expansion.current_offset).line
        return self._logical_states.get(physical_line)

    def get(self, name: str) -> MacroState:
        if name in self._explicit_names:
            return self._base.get(name)
        if name == "__LINE__":
            state = self._logical_state()
            if state is None:
                return MacroState(True, None)
            definition = MacroDefinition(name, str(state.line))
            return MacroState(True, state.line != 0, definition)
        if name == "__FILE__":
            state = self._logical_state()
            if state is None or state.file_literal is None:
                return MacroState(True, None)
            return MacroState(True, None, MacroDefinition(name, state.file_literal))
        if name in {"__DATE__", "__TIME__"}:
            # The names are standard predefined macros, but their values are
            # intentionally unavailable until the caller supplies deterministic text.
            return MacroState(True, None)
        return self._base.get(name)

    def define(self, definition: MacroDefinition) -> None:
        self._base.define(definition)
        self._explicit_names.add(definition.name)

    def undef(self, name: str) -> None:
        self._base.undef(name)
        self._explicit_names.add(name)

    def snapshot(self) -> Mapping[str, MacroState]:
        return self._base.snapshot()


def _configured_preprocessing_context(
    environment: MacroEnvironment,
    context: PreprocessingContext | None,
) -> None:
    if context is None:
        return
    if not isinstance(context, PreprocessingContext):
        raise AnalysisError(
            "context must be a PreprocessingContext instance",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    existing = environment.snapshot()
    for name, replacement in context.standard_macros.items():
        if name in existing:
            raise AnalysisError(
                f"preprocessing context conflicts with configured macro: {name}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
        environment.define(MacroDefinition(name, replacement))


def _file_literal(filename: str | None) -> str | None:
    if filename is None:
        return None
    return json.dumps(filename, ensure_ascii=False)


def _line_directive_operands(
    text: str,
    environment: _PredefinedMacroEnvironment,
    state: _LogicalPreprocessingState,
    budget: AnalysisBudget,
) -> list[Token]:
    """Macro-expand a #line operand list before interpreting its grammar."""
    fragment = Expansion(text, budget)
    tokens = [token for token in fragment.tokens if token.kind not in {"space", "comment"}]
    previous = environment.logical_override
    environment.logical_override = state
    try:
        expanded = fragment._expand(tokens, environment)  # internal shared expansion semantics
    finally:
        environment.logical_override = previous
    return [token for token in expanded if token.kind != "empty"]


def _parse_line_directive(
    concrete: str,
    environment: _PredefinedMacroEnvironment,
    state: _LogicalPreprocessingState,
    budget: AnalysisBudget,
) -> tuple[int, str | None]:
    head = directive_head(concrete)
    if head is None or head.name != "line":
        raise ValueError("malformed #line directive")
    tokens = _line_directive_operands(concrete[head.name_end :], environment, state, budget)
    if not 1 <= len(tokens) <= 2:
        raise ValueError('#line expects a decimal line number and optional "filename"')
    number = tokens[0]
    if number.kind != "number" or re.fullmatch(r"[0-9]+", number.text) is None:
        raise ValueError("#line line number must expand to a decimal integer")
    line_number = int(number.text, 10)
    if line_number <= 0 or line_number > 2_147_483_647:
        raise ValueError("#line line number is outside the supported standard range")
    file_literal = None
    if len(tokens) == 2:
        candidate = tokens[1]
        if (
            candidate.kind != "literal"
            or re.fullmatch(r'"(?:\\.|[^"\\])*"', candidate.text, re.DOTALL) is None
        ):
            raise ValueError("#line filename must expand to an ordinary string literal")
        file_literal = candidate.text
    return line_number, file_literal


def _unconfigured_predefined_macro(text: str, environment: MacroLookup) -> str | None:
    """Return the first predefined macro whose replacement value is required."""
    tokens = [token for token in tokenize(text) if token.kind not in {"space", "comment"}]
    defined_operands: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or token.text != "defined":
            continue
        candidate = index + 1
        if candidate < len(tokens) and tokens[candidate].text == "(":
            candidate += 1
        if candidate < len(tokens) and tokens[candidate].kind == "identifier":
            defined_operands.add(candidate)

    for index, token in enumerate(tokens):
        if index in defined_operands:
            continue
        if token.kind != "identifier" or token.text not in _PREDEFINED_MACROS:
            continue
        if environment.get(token.text).definition is None:
            return token.text
    return None


def _unexpanded_predefined_macro(
    output: str,
    source_map: tuple[SourceMapping, ...],
    expansion: Expansion,
) -> tuple[str, SourceLocation] | None:
    """Find a predefined macro that survived supported expansion and map its origin."""
    for token in tokenize(output):
        if token.kind != "identifier" or token.text not in _PREDEFINED_MACROS:
            continue
        for mapping in source_map:
            if not (mapping.output_start <= token.start < mapping.output_end):
                continue
            if mapping.expanded:
                return token.text, mapping.start
            source_offset = mapping.source_start + (token.start - mapping.output_start)
            return token.text, expansion.location(source_offset)
        return token.text, SourceLocation(1)
    return None


_INCLUDE_DIRECTIVES = frozenset(("include_next", "include", "import"))


def _include_directive(concrete: str) -> tuple[str, str] | None:
    """Return ``(kind, operand)`` for a spliced include-family directive."""
    head = directive_head(concrete)
    if head is None or head.name not in _INCLUDE_DIRECTIVES:
        return None
    return head.name, concrete[head.name_end :].strip()


def _include_header(
    raw: str,
    operand: str,
    environment: _PredefinedMacroEnvironment,
    state: _LogicalPreprocessingState,
    budget: AnalysisBudget,
) -> tuple[str, IncludeForm]:
    """Interpret an include operand as a header name.

    Direct ``"header"`` and ``<header>`` forms are read from the physical spelling
    so header-name characters such as ``//`` are not lexed as comments. Any other
    operand is macro expanded and must produce one of those forms.
    """
    spliced = splice(raw).rstrip("\r\n")
    head = directive_head(spliced)
    if head is not None and head.name in _INCLUDE_DIRECTIVES:
        rest = spliced[head.name_end :].lstrip(HORIZONTAL_WHITESPACE)
        direct = re.match(r'"([^"\r\n]*)"|<([^>\r\n]*)>', rest)
        if direct is not None:
            trailing = tokenize(rest[direct.end() :])
            if any(token.kind not in {"space", "comment"} for token in trailing):
                raise ValueError("unexpected tokens after the include header name")
            quoted = direct[1] is not None
            header = direct[1] if quoted else direct[2]
            if not header:
                raise ValueError("empty include header name")
            return header, IncludeForm.QUOTED if quoted else IncludeForm.ANGLE

    fragment = Expansion(operand, budget)
    tokens = [token for token in fragment.tokens if token.kind not in {"space", "comment"}]
    previous = environment.logical_override
    environment.logical_override = state
    try:
        expanded = fragment._expand(tokens, environment)  # internal shared expansion semantics
    finally:
        environment.logical_override = previous
    expanded = [token for token in expanded if token.kind not in {"empty", "space", "comment"}]
    if (
        len(expanded) == 1
        and expanded[0].kind == "literal"
        and re.fullmatch(r'"[^"\r\n]*"', expanded[0].text)
    ):
        header = expanded[0].text[1:-1]
        form = IncludeForm.QUOTED
    elif (
        len(expanded) >= 3
        and expanded[0].text == "<"
        and expanded[-1].text == ">"
        and not any(token.text == ">" for token in expanded[1:-1])
    ):
        header = "".join(token.text for token in expanded[1:-1])
        form = IncludeForm.ANGLE
    else:
        raise ValueError('include operand must be "header" or <header>, or macro-expand to one')
    if not header:
        raise ValueError("empty include header name")
    return header, form


def compact(
    result: PreprocessResult,
    *,
    max_consecutive_blank_lines: int = 0,
) -> str:
    """Return an explicitly requested compact view of a completed result.

    Only physical lines recorded in ``result.removed_lines`` are eligible for
    removal or collapse. Retained lines, including intentional blank lines, are
    emitted byte-for-byte as represented in ``result.source``. ``source_map``
    continues to describe only the canonical coordinate-preserving source.
    """
    if max_consecutive_blank_lines < 0:
        raise ValueError("max_consecutive_blank_lines must be non-negative")
    if not result.complete or result.source is None or result.removed_lines is None:
        raise ValueError("compact() requires a complete PreprocessResult")

    output: list[str] = []
    removed_run = 0
    for line_number, line in enumerate(split_lines(result.source, keepends=True), 1):
        if line_number not in result.removed_lines:
            output.append(line)
            removed_run = 0
            continue

        if removed_run < max_consecutive_blank_lines:
            if line.endswith("\r\n"):
                output.append("\r\n")
            elif line.endswith("\n"):
                output.append("\n")
            elif line.endswith("\r"):
                output.append("\r")
        removed_run += 1
    return "".join(output)


@dataclass(frozen=True)
class PreprocessRequest:
    """Everything one concrete preprocessing run needs; the core consumes only this.

    The public functions (:func:`preprocess_source`, ``cpre.pragmas.preprocess_source``) are
    adapters that build a request. ``dispatch_included_pragmas`` is set by the pragma layer
    so included ``#pragma`` directives become markers it can deliver in output order;
    ``protected_names`` is set by ``MacroConfiguration.from_source`` for seed evaluation.

    This is internal, not part of the public API (hence absent from ``__all__``):
    ``dispatch_included_pragmas`` is only sound when the pragma layer consumes the markers it
    produces, so use :func:`preprocess_source` or ``cpre.pragmas.preprocess_source`` instead.
    """

    source: str
    filename: str | None = None
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None
    configuration: MacroConfiguration | None = None
    context: PreprocessingContext | None = None
    options: AnalysisOptions | None = None
    skip_includes: bool = False
    include_resolver: IncludeResolver | None = None
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH
    include_query: _has_include.IncludeQueryProvider | None = None
    has_include_from_resolver: bool = False
    dispatch_included_pragmas: bool = False
    protected_names: frozenset[str] | None = None


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    configuration: MacroConfiguration | None = None,
    context: PreprocessingContext | None = None,
    include_query: _has_include.IncludeQueryProvider | None = None,
    options: AnalysisOptions | None = None,
    skip_includes: bool = False,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
    has_include_from_resolver: bool = False,
) -> PreprocessResult:
    """Select conditional branches under an explicit concrete macro state.

    ``assumptions`` preserves the existing open-world Boolean contract: unmentioned
    macros remain unknown and assumed values do not provide replacement text.
    ``configuration`` instead seeds concrete external macro definitions and may
    opt into closed-world unknown-name handling. The two inputs are mutually
    exclusive so Boolean constraints cannot silently disagree with replacement
    definitions. ``context`` supplies deterministic standard predefined-macro
    replacement text; location-sensitive ``__LINE__``/``__FILE__`` use logical
    preprocessing state derived from the physical source, ``filename``, and active
    standard ``#line`` directives.

    Reachable integer expressions are macro expanded and evaluated concretely when
    Boolean reasoning alone cannot choose a branch. A still-undecidable condition
    returns an incomplete result with ``source=None``. Inactive text and
    conditional directives become spaces, preserving physical line endings.
    Unexpanded spans map one-to-one. Block comments overlapping retained text are
    kept whole to balance delimiters. Macros in retained text expand with invocation
    provenance in source_map. Active define/undef directives update macro state and
    override externally configured state in source order; they are masked in the
    output. Standard ``#line`` directives update only logical line/file state and are
    also masked. Includes, reachable unsupported nonconditional directives, and
    reachable predefined macro value uses without deterministic replacement semantics
    return atomic incomplete results. With ``skip_includes=True``, active
    ``#include``/``#include_next``/``#import`` directives are instead masked like other
    handled directives and reported in ``skipped_includes``; the skipped headers are
    never read, so no macros or declarations are attributed to them. Under the
    closed-world policy, names neither configured explicitly nor already settled by
    the source become unknown after the first reachable skipped include.

    With ``include_resolver``, reachable include directives are resolved by the
    caller and the resolved sources are preprocessed recursively with shared macro
    state. Their canonical output is spliced after the masked directive line and
    mapped with ``SourceMapping.source_identity``. Cycles, nesting deeper than
    ``max_include_depth``, and unresolvable headers return atomic incomplete
    results; unresolvable headers fall back to masking when ``skip_includes`` is
    also set. ``#pragma once``, ``#import``, and detected include guards prevent
    re-entering a source. Every resolved include is reported in ``includes``.

    Definedness-only checks remain ordinary conditional reasoning. Successful
    results expose a detached, read-only final macro-state snapshot and immutable
    provenance for output lines wholly removed by preprocessing. Malformed
    conditionals raise the same structured ParseError as analyze_source.

    cpre never searches the filesystem or emulates compiler include paths. When a
    reachable condition requires ``__has_include``, ``include_query`` receives the
    macro-expanded header spelling, delimiter form, physical source location, and
    the identity of the source containing the query: ``filename`` for the primary
    source, or the resolved identity of an included source. A Boolean answer is
    substituted deterministically; ``None`` yields an atomic incomplete result.
    Queries in branches or Boolean terms proven unreachable are never sent to the
    provider. With ``has_include_from_resolver=True``, a query that
    ``include_query`` does not answer (no callback, or ``None``) is answered by
    ``include_resolver``: the header is available exactly when the resolver returns
    a :class:`ResolvedInclude` for the equivalent ``#include`` request. The resolver
    must have no side effects for this to be sound, so it is opt-in.
    ``defined(__has_include)`` is true when either source of answers is enabled;
    ``__has_include_next`` is unsupported.
    """
    return preprocess_request(
        PreprocessRequest(
            source,
            filename=filename,
            assumptions=assumptions,
            configuration=configuration,
            context=context,
            options=options,
            skip_includes=skip_includes,
            include_resolver=include_resolver,
            max_include_depth=max_include_depth,
            include_query=include_query,
            has_include_from_resolver=has_include_from_resolver,
        )
    )


def preprocess_request(request: PreprocessRequest) -> PreprocessResult:
    """Run the concrete preprocessor for one request (the only core entry point)."""
    source = request.source
    filename = request.filename
    assumptions = request.assumptions
    configuration = request.configuration
    context = request.context
    options = request.options
    skip_includes = request.skip_includes
    include_resolver = request.include_resolver
    max_include_depth = request.max_include_depth
    include_query = request.include_query
    has_include_from_resolver = request.has_include_from_resolver
    if include_query is not None and not callable(include_query):
        raise AnalysisError(
            "include_query must be callable",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    if type(skip_includes) is not bool:
        raise AnalysisError(
            "skip_includes must be True or False",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    if include_resolver is not None and not callable(include_resolver):
        raise AnalysisError(
            "include_resolver must be callable",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    if type(has_include_from_resolver) is not bool:
        raise AnalysisError(
            "has_include_from_resolver must be True or False",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    if has_include_from_resolver and include_resolver is None:
        raise AnalysisError(
            "has_include_from_resolver requires an include_resolver",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    if type(max_include_depth) is not int or max_include_depth < 1:
        raise AnalysisError(
            "max_include_depth must be a positive integer",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    normalized = _normalize_assumptions(assumptions)
    if configuration is not None and normalized is not None:
        raise AnalysisError(
            "assumptions and configuration are mutually exclusive",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    base_environment = (
        _configured_environment(configuration)
        if configuration is not None
        else MacroEnvironment(normalized)
    )
    _configured_preprocessing_context(base_environment, context)
    resolved_options = options if options is not None else AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance", code=ErrorCode.ANALYSIS_FAILURE
        )

    limits = resolved_options._resource_limits()
    includes = (
        _IncludeState(
            include_resolver,
            max_include_depth,
            [filename] if filename is not None else [],
        )
        if include_resolver is not None
        else None
    )
    run = _Run(
        base_environment,
        limits,
        AnalysisBudget(limits.max_work),
        skip_includes,
        includes,
        [],
        request.dispatch_included_pragmas,
        include_query,
        has_include_from_resolver,
        request.protected_names,
    )
    unit = _preprocess_unit(source, run, identity=None, filename=filename, depth=0)
    if unit.diagnostics:
        diagnostics = list(unit.diagnostics)
        diagnostics.sort(key=lambda item: item.location.line if item.location else 0)
        return PreprocessResult(None, filename, tuple(diagnostics))
    return PreprocessResult(
        unit.source,
        filename,
        macros=base_environment.snapshot(),
        source_map=unit.source_map,
        removed_lines=unit.removed_lines,
        skipped_includes=tuple(run.skipped),
        includes=tuple(includes.records) if includes is not None else (),
        _included_pragmas=unit.pragmas,
    )


class _UnitContext:
    """What the parts of one source's preprocessing share.

    The primary source and every included source each get a context. It holds the
    per-unit state the old monolithic function kept in local variables: the physical
    and logical line tables, which lines survive, the diagnostics and include
    insertions collected so far, and the macro environment and ``__has_include``
    answers that conditions are evaluated against. The parts that use it are
    :class:`_QueryAnswerer`, :class:`_BranchSelector`, :class:`_IncludeHandler` and
    :class:`_UnitProcessor`; each can be built and called on its own.
    """

    def __init__(
        self,
        source: str,
        run: _Run,
        *,
        identity: str | None,
        filename: str | None,
        depth: int,
        queries: tuple[_has_include.Occurrence, ...] = (),
    ) -> None:
        self.source = source
        self.run = run
        self.identity = identity
        self.filename = filename
        self.depth = depth
        self.queries_by_line: dict[int, list[_has_include.Occurrence]] = {}
        for occurrence in queries:
            self.queries_by_line.setdefault(occurrence.directive_line, []).append(occurrence)
        self.base_environment = run.base_environment
        self.limits = run.limits
        self.budget = run.budget
        self.includes = run.includes
        self.physical = split_lines(source, keepends=True)
        self.logical = list(logical_lines(source))
        self.ends = {line.start_line: line.end_line for line in self.logical}
        self.retained = [True] * len(self.physical)
        self.diagnostics: list[PreprocessDiagnostic | AnalysisIncomplete] = []
        self.insertions: list[tuple[int, int, _UnitOutput]] = []
        self.expansion = Expansion(source, self.budget)
        self.logical_states: dict[int, _LogicalPreprocessingState] = {}
        self.environment = _PredefinedMacroEnvironment(
            self.base_environment, self.expansion, self.logical_states
        )
        self.view = _has_include.ConditionView(
            self.environment,
            queries,
            run.include_query is not None or run.has_include_from_resolver,
        )
        self.offsets = [0]
        for physical_line in self.physical:
            self.offsets.append(self.offsets[-1] + len(physical_line))
        self.current_line: int | None = None
        self.logical_delta = 0
        self.logical_file_literal = _file_literal(filename)

    def blank(self, start: int, end: int) -> None:
        """Drop physical lines ``start`` through ``end`` (1-based, inclusive) from the output."""
        self.retained[start - 1 : end] = [False] * (end - start + 1)

    def located(self, code: ErrorCode, message: str, line: int) -> PreprocessDiagnostic:
        return PreprocessDiagnostic(code, message, SourceLocation(line), self.identity)

    def limit_diagnostic(
        self, error: AnalysisLimitExceeded, location: SourceLocation | None
    ) -> AnalysisIncomplete:
        return AnalysisIncomplete(
            ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
            error.resource,
            error.limit,
            error.observed,
            str(error),
            location,
            source_identity=self.identity,
        )


class _QueryAnswerer:
    """Answers one reachable ``__has_include`` from the caller's query and resolver."""

    def __init__(self, context: _UnitContext) -> None:
        self.context = context

    def _diagnose(self, code: ErrorCode, message: str, occurrence: _has_include.Occurrence) -> None:
        context = self.context
        context.diagnostics.append(
            PreprocessDiagnostic(code, message, occurrence.location, context.identity)
        )

    def answer(self, occurrence: _has_include.Occurrence) -> bool:
        """Answer one reachable __has_include; False means diagnostics were added."""
        context = self.context
        run = context.run
        if occurrence.operator == _has_include.HAS_INCLUDE_NEXT:
            self._diagnose(
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                "__has_include_next is not supported during concrete preprocessing",
                occurrence,
            )
            return False
        if occurrence.operand is None:
            self._diagnose(
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                "__has_include expects one parenthesized header-name operand",
                occurrence,
            )
            return False
        if run.include_query is None and not run.has_include_from_resolver:
            self._diagnose(
                ErrorCode.UNRESOLVED_CONDITION,
                "__has_include requires caller-provided include availability",
                occurrence,
            )
            return False
        try:
            query = _has_include.header_query(
                occurrence, context.environment, context.budget, context.filename
            )
        except _has_include.HeaderOperandError as error:
            self._diagnose(ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION, str(error), occurrence)
            return False
        except ExpansionError as error:
            self._diagnose(ErrorCode.UNSUPPORTED_MACRO_EXPANSION, str(error), occurrence)
            return False
        except AnalysisLimitExceeded as error:
            context.diagnostics.append(context.limit_diagnostic(error, occurrence.location))
            return False
        available = self._provider_answer(query, occurrence)
        if available is None and run.has_include_from_resolver:
            available = self._resolver_answer(query, occurrence)
        if available is None:
            delimiter = (
                f'"{query.header}"' if query.form is IncludeForm.QUOTED else f"<{query.header}>"
            )
            self._diagnose(
                ErrorCode.UNRESOLVED_CONDITION,
                f"include availability is unknown for {delimiter}",
                occurrence,
            )
            return False
        context.view.answers[occurrence.placeholder] = available
        return True

    def _provider_answer(
        self, query: _has_include.IncludeQuery, occurrence: _has_include.Occurrence
    ) -> bool | None:
        context = self.context
        provider = context.run.include_query
        available = provider(query) if provider is not None else None
        if available is not None and type(available) is not bool:
            raise AnalysisError(
                "include_query must return True, False, or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=occurrence.location,
                filename=context.filename,
            )
        return available

    def _resolver_answer(
        self, query: _has_include.IncludeQuery, occurrence: _has_include.Occurrence
    ) -> bool:
        """Available exactly when the equivalent #include would resolve; quoted forms
        search relative to the source containing the query."""
        context = self.context
        includes = context.includes
        assert includes is not None
        includer = context.identity if context.identity is not None else context.filename
        request = IncludeRequest(
            query.header, query.form, "include", occurrence.location, includer, context.depth
        )
        resolved = includes.resolver(request)
        if resolved is not None and not isinstance(resolved, ResolvedInclude):
            raise AnalysisError(
                "include_resolver must return a ResolvedInclude or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=occurrence.location,
                filename=includer,
            )
        return resolved is not None


class _BranchSelector:
    """Decides which reachable branch of a conditional group is taken.

    A condition is decided in this order: the Boolean check against the macro state
    (ROBDD), then concrete numeric evaluation, then - for a condition left
    undetermined by an unanswered ``__has_include`` - the first such query on the
    line is asked and the condition is decided again, so queries in irrelevant
    Boolean terms are never sent. A condition that stays undetermined gets the
    unresolved-condition diagnostic.
    """

    def __init__(self, context: _UnitContext, answerer: _QueryAnswerer) -> None:
        self.context = context
        self.answerer = answerer

    def select(self, branch: ConditionalBranch) -> bool | None:
        """Decide one reachable branch; None means diagnostics were added."""
        context = self.context
        builtin = (
            _unconfigured_predefined_macro(branch.expression_text, context.view)
            if branch.expression_text is not None
            and branch.directive not in _DEFINEDNESS_DIRECTIVES
            else None
        )
        if builtin is not None:
            context.diagnostics.append(
                context.located(
                    ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                    f"predefined macro {builtin} is not supported during concrete preprocessing",
                    branch.line,
                )
            )
            return None
        condition = branch.expression if branch.expression is not None else TRUE
        # The macro context constrains each name independently (C semantics couple
        # only a name's value and definedness), so whether it decides the condition
        # depends on the condition's own names alone. A manager per condition keeps
        # max_atoms and max_bdd_nodes from scaling with the number of conditionals
        # in the file; max_work stays shared by the whole run.
        atoms = list(expression_atoms_in_order(condition))
        names = sorted({atom.name for atom in atoms if isinstance(atom, Variable)})
        semantics = conjunction(
            *(disjunction(negate(Variable(name)), DefinedVariable(name)) for name in names)
        )
        bdd = BDD(
            [*atoms, *expression_atoms_in_order(semantics)],
            limits=context.limits,
            budget=context.budget,
        )
        line_queries = context.queries_by_line.get(branch.line, [])
        while True:
            boolean = self._boolean(bdd, condition, semantics, names)
            if boolean is not None:
                return boolean
            pending = next(
                (item for item in line_queries if item.placeholder not in context.view.answers),
                None,
            )
            if branch.expression_text is not None and branch.directive not in (
                _DEFINEDNESS_DIRECTIVES
            ):
                try:
                    selected = self._numeric(branch, pending is not None)
                except _Undecidable:
                    return None
                if selected is not None:
                    return selected
            if pending is not None:
                if not self.answerer.answer(pending):
                    return None
                continue
            context.diagnostics.append(self._unresolved(branch, line_queries))
            return None

    def _boolean(
        self,
        bdd: BDD,
        condition: Expression,
        semantics: Expression,
        names: list[str],
    ) -> bool | None:
        """True or False when the macro state decides ``condition``; None when it does not."""
        terms = [semantics]
        for name in names:
            macro = self.context.view.get(name)
            for atom, value in (
                (DefinedVariable(name), macro.defined),
                (Variable(name), macro.value),
            ):
                if value is not None:
                    terms.append(atom if value else negate(atom))
        context_expression = conjunction(*terms)
        if not bdd.satisfiable(conjunction(context_expression, condition)):
            return False
        if not bdd.satisfiable(conjunction(context_expression, negate(condition))):
            return True
        return None

    def _numeric(self, branch: ConditionalBranch, query_pending: bool) -> bool | None:
        """Evaluate the condition concretely; None when a pending query may still decide it.

        Raises :class:`_Undecidable` after recording the diagnostic otherwise.
        """
        context = self.context
        assert branch.expression_text is not None
        try:
            return evaluate_numeric_condition(
                branch.expression_text,
                _condition_environment(context.view),
                context.expansion,
                context.budget,
                unsupported_identifiers=_PREDEFINED_MACROS,
            )
        except NumericConditionError as error:
            if query_pending:
                return None
            context.diagnostics.append(
                context.located(ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION, str(error), branch.line)
            )
        except ExpansionError as error:
            context.diagnostics.append(
                context.located(ErrorCode.UNSUPPORTED_MACRO_EXPANSION, str(error), branch.line)
            )
        raise _Undecidable

    def _unresolved(
        self, branch: ConditionalBranch, line_queries: list[_has_include.Occurrence]
    ) -> PreprocessDiagnostic:
        context = self.context
        condition_text = (
            _has_include.restore(branch.expression_text, line_queries)
            if branch.expression_text is not None
            else None
        )
        return _unresolved_condition(
            branch.directive,
            condition_text,
            context.view,
            branch.line,
            context.identity,
            context.limits.max_work,
        )


class _Undecidable(Exception):
    """A branch condition could not be decided; the diagnostic is already recorded."""


class _IncludeHandler:
    """Resolves, tracks and splices one reachable ``#include``, ``#include_next`` or ``#import``."""

    def __init__(self, context: _UnitContext) -> None:
        self.context = context

    def skip(self, kind: str, operand: str, location: SourceLocation) -> None:
        context = self.context
        context.run.skipped.append(SkippedInclude(kind, operand, location, context.identity))
        if isinstance(context.base_environment, _ConfiguredMacroEnvironment):
            # Names the caller configured or the source already settled keep their
            # state; any other name may come from the unread header.
            context.base_environment._skip_include()

    def handle(self, kind: str, operand: str, line: int, state: _LogicalPreprocessingState) -> bool:
        """Handle one reachable include directive; False means diagnostics were added."""
        context = self.context
        location = SourceLocation(line)
        if not operand:
            context.diagnostics.append(
                context.located(
                    ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                    f"#{kind} expects a header name",
                    line,
                )
            )
            return False
        if context.includes is None:
            self.skip(kind, operand, location)
            context.blank(line, context.ends[line])
            return True
        return self._resolve(kind, operand, line, state, location)

    def _resolve(
        self,
        kind: str,
        operand: str,
        line: int,
        state: _LogicalPreprocessingState,
        location: SourceLocation,
    ) -> bool:
        context = self.context
        includes = context.includes
        assert includes is not None
        ends = context.ends
        offsets = context.offsets
        context.expansion.current_offset = offsets[line - 1]
        try:
            header, form = _include_header(
                context.source[offsets[line - 1] : offsets[ends[line]]],
                operand,
                context.environment,
                state,
                context.budget,
            )
        except ValueError as error:
            context.diagnostics.append(
                context.located(ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, str(error), line)
            )
            return False
        spelling = f'"{header}"' if form is IncludeForm.QUOTED else f"<{header}>"
        includer = context.identity if context.identity is not None else context.filename
        request = IncludeRequest(header, form, kind, location, includer, context.depth)
        resolved = includes.resolver(request)
        if resolved is None:
            if context.run.skip_includes:
                self.skip(kind, operand, location)
                context.blank(line, ends[line])
                return True
            context.diagnostics.append(
                context.located(
                    ErrorCode.UNRESOLVED_INCLUDE, f"cannot resolve #{kind} {spelling}", line
                )
            )
            return False
        if not isinstance(resolved, ResolvedInclude):
            raise AnalysisError(
                "include_resolver must return a ResolvedInclude or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=location,
                filename=includer,
            )
        target = resolved.identity
        outcome = self._skip_outcome(kind, resolved)
        if kind == "import":
            includes.imported.add(target)
        if outcome is not None:
            includes.records.append(IncludeRecord(request, target, outcome))
            context.blank(line, ends[line])
            return True
        return self._enter(kind, spelling, line, request, resolved)

    def _skip_outcome(self, kind: str, resolved: ResolvedInclude) -> IncludeOutcome | None:
        """Why this include is not entered again (``#pragma once``, ``#import``, guard)."""
        context = self.context
        includes = context.includes
        assert includes is not None
        target = resolved.identity
        if target in includes.once:
            return IncludeOutcome.PRAGMA_ONCE
        if target in includes.entered and (kind == "import" or target in includes.imported):
            # #import makes a source include-once, as in GCC and Clang.
            return IncludeOutcome.IMPORTED
        if target not in includes.guards:
            includes.guards[target] = _detect_include_guard(resolved.source)
        guard = includes.guards[target]
        if guard is not None and context.environment.get(guard).defined is True:
            return IncludeOutcome.GUARDED
        return None

    def _enter(
        self,
        kind: str,
        spelling: str,
        line: int,
        request: IncludeRequest,
        resolved: ResolvedInclude,
    ) -> bool:
        """Preprocess the included source and splice its output after the directive."""
        context = self.context
        includes = context.includes
        assert includes is not None
        ends = context.ends
        target = resolved.identity
        if target in includes.stack:
            context.diagnostics.append(
                context.located(
                    ErrorCode.INCLUDE_CYCLE,
                    f"#{kind} {spelling} re-enters {target}, which is already being preprocessed",
                    line,
                )
            )
            return False
        if context.depth + 1 > includes.max_depth:
            context.diagnostics.append(
                context.located(
                    ErrorCode.INCLUDE_DEPTH_EXCEEDED,
                    f"#{kind} {spelling} exceeds the maximum include depth of {includes.max_depth}",
                    line,
                )
            )
            return False

        guard = includes.guards[target]
        if guard is not None and context.environment.get(guard).defined is None:
            # A detected include guard is file-private, as in
            # MacroConfiguration.from_source(): unless the caller configured it,
            # it starts undefined so the guarded body is entered.
            context.environment.undef(guard)
        includes.records.append(IncludeRecord(request, target, IncludeOutcome.ENTERED))
        includes.entered.add(target)
        includes.stack.append(target)
        try:
            child = _preprocess_unit(
                resolved.source,
                context.run,
                identity=target,
                filename=target,
                depth=context.depth + 1,
            )
        finally:
            includes.stack.pop()
        # Names defined or undefined by the included source are explicit state now.
        context.environment._explicit_names.update(context.base_environment.snapshot())
        if child.diagnostics:
            context.diagnostics.extend(child.diagnostics)
            return False
        context.blank(line, ends[line])
        context.insertions.append((context.offsets[ends[line]], ends[line], child))
        return True


class _UnitProcessor:
    """Walks the logical lines of one source: conditionals, directives and text."""

    def __init__(self, context: _UnitContext, tree: ConditionalTree) -> None:
        self.context = context
        self.answerer = _QueryAnswerer(context)
        self.selector = _BranchSelector(context, self.answerer)
        self.includes = _IncludeHandler(context)
        self.starts = {group.line: group for group in tree.groups}
        self._index_groups(tree.groups)
        self.branches = {
            branch.line: branch for group in self.starts.values() for branch in group.branches
        }
        self.selections = _SELECTED_BRANCHES.get() if context.identity is None else None
        # One frame per open conditional group: [entered active, a branch was taken,
        # the current branch is active].
        self.stack: list[list[bool]] = []
        self.group_lines: list[int] = []
        self.active = True

    def _index_groups(self, groups: list[ConditionalGroup]) -> None:
        for group in groups:
            self.starts[group.line] = group
            for branch in group.branches:
                self._index_groups(branch.children)

    def run(self) -> None:
        """Process every logical line, leaving results in the context.

        Stops at the first line that adds a diagnostic.
        """
        context = self.context
        try:
            for line in context.logical:
                if not self._line(line):
                    break
            if not context.diagnostics:
                context.environment.logical_override = None
                context.expansion.flush(context.environment)
        except ExpansionError as error:
            context.diagnostics.append(
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                    str(error),
                    context.expansion.location(context.expansion.current_offset),
                    context.identity,
                )
            )
        except AnalysisLimitExceeded as error:
            limit_line = error.line if error.line is not None else context.current_line
            context.diagnostics.append(
                context.limit_diagnostic(
                    error, SourceLocation(limit_line) if limit_line is not None else None
                )
            )

    def _line(self, line: LogicalLine) -> bool:
        """Process one logical line; False means a diagnostic ended the walk."""
        context = self.context
        current_line = line.start_line
        context.current_line = current_line
        for physical_line_number in range(current_line, context.ends[current_line] + 1):
            context.logical_states[physical_line_number] = _LogicalPreprocessingState(
                physical_line_number + context.logical_delta,
                context.logical_file_literal,
            )
        line_state = context.logical_states[current_line]
        head = line.directive
        if head is not None:
            context.environment.logical_override = None
            context.expansion.flush(context.environment)
        branch = self.branches.get(current_line)
        if branch is not None:
            return self._conditional_line(branch, current_line, line_state)
        core = head.name if head is not None and head.name in _CORE_DIRECTIVES else None
        if core == "endif":
            self.stack.pop()
            self.group_lines.pop()
            self.active = self.stack[-1][2] if self.stack else True
            context.blank(current_line, context.ends[current_line])
            return True
        if not self.active:
            context.blank(current_line, context.ends[current_line])
            return True
        if core is not None:
            return self._core_directive(core, current_line, line_state)
        if head is not None:
            return self._other_directive(current_line, line_state)
        context.environment.logical_override = None
        context.expansion.line(
            context.offsets[current_line - 1], context.offsets[context.ends[current_line]]
        )
        return True

    def _conditional_line(
        self, branch: ConditionalBranch, current_line: int, line_state: _LogicalPreprocessingState
    ) -> bool:
        context = self.context
        if current_line in self.starts:
            self.stack.append([self.active, False, False])
            self.group_lines.append(current_line)
            if self.selections is not None and self.active:
                self.selections[current_line] = None
        frame = self.stack[-1]
        self.active = False
        context.blank(current_line, context.ends[current_line])
        context.environment.logical_override = line_state
        try:
            if frame[0] and not frame[1]:
                selected = self.selector.select(branch)
                if selected is None:
                    return False
                if selected:
                    self.active = True
                    frame[1] = True
                    if self.selections is not None:
                        self.selections[self.group_lines[-1]] = current_line
        finally:
            context.environment.logical_override = None
        frame[2] = self.active
        return True

    def _core_directive(
        self, kind: str, current_line: int, line_state: _LogicalPreprocessingState
    ) -> bool:
        """``#define``, ``#undef``, ``#line`` and the include family on an active line."""
        context = self.context
        run = context.run
        definition = None
        include: tuple[str, str] | None = None
        try:
            concrete = context.expansion.directive_text(
                context.offsets[current_line - 1], context.offsets[context.ends[current_line]]
            )
            if kind == "line":
                self._line_directive(concrete, current_line, line_state)
                return True
            if kind not in {"define", "undef"}:
                if not run.skip_includes and context.includes is None:
                    raise ValueError("include processing is not supported")
                include = _include_directive(concrete)
                if include is None or include[0] != kind:
                    raise ValueError("ambiguous directive after physical line splicing")
            else:
                head = directive_head(concrete)
                if head is None or head.name != kind:
                    raise ValueError("ambiguous directive after physical line splicing")
                remainder = concrete[head.name_end :]
                if self._protected(kind, remainder, current_line):
                    return False
                _apply_macro_directive(
                    context.environment, kind, remainder, SourceLocation(current_line)
                )
                if kind == "define":
                    defined_name = macro_name(remainder)
                    assert defined_name is not None
                    definition = context.environment.get(defined_name).definition
        except ValueError as error:
            context.diagnostics.append(
                context.located(
                    ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, str(error), current_line
                )
            )
            return False
        if include is not None:
            return self.includes.handle(kind, include[1], current_line, line_state)
        if definition is not None:
            context.expansion.current_offset = context.offsets[current_line - 1]
            context.expansion.validate_definition(definition)
        context.blank(current_line, context.ends[current_line])
        return True

    def _line_directive(
        self, concrete: str, current_line: int, line_state: _LogicalPreprocessingState
    ) -> None:
        """Apply a standard ``#line``: it only changes logical line and file state."""
        context = self.context
        context.expansion.current_offset = context.offsets[current_line - 1]
        next_line, next_file_literal = _parse_line_directive(
            concrete, context.environment, line_state, context.budget
        )
        context.logical_delta = next_line - (context.ends[current_line] + 1)
        if next_file_literal is not None:
            context.logical_file_literal = next_file_literal
        context.blank(current_line, context.ends[current_line])

    def _protected(self, kind: str, remainder: str, current_line: int) -> bool:
        """Diagnose a seed ``#define``/``#undef`` of a name the context supplies."""
        context = self.context
        protected = context.run.protected_names
        if not protected:
            return False
        target = macro_name(remainder)
        if target is None or target not in protected:
            return False
        verb = "redefines" if kind == "define" else "undefines"
        context.diagnostics.append(
            context.located(
                ErrorCode.INVALID_CONFIGURATION,
                f"seed {verb} {target}, which the preprocessing context supplies",
                current_line,
            )
        )
        return True

    def _other_directive(self, current_line: int, line_state: _LogicalPreprocessingState) -> bool:
        """The null directive, ``#pragma once``, skipped includes; anything else is unsupported."""
        context = self.context
        includes = context.includes
        concrete = context.expansion.directive_text(
            context.offsets[current_line - 1], context.offsets[context.ends[current_line]]
        )
        head = directive_head(concrete)
        assert head is not None  # the logical line is a directive line
        operand = concrete[head.name_end :]
        if head.name is None and not operand:
            context.blank(current_line, context.ends[current_line])
            return True
        if context.identity is not None and includes is not None:
            if (
                head.name == "pragma"
                and operand[:1] in tuple(HORIZONTAL_WHITESPACE)
                and operand.strip(HORIZONTAL_WHITESPACE) == "once"
            ):
                includes.once.add(context.identity)
                context.blank(current_line, context.ends[current_line])
                return True
        if context.run.skip_includes or includes is not None:
            include = _include_directive(concrete)
            if include is not None:
                return self.includes.handle(include[0], include[1], current_line, line_state)
        if head.name is not None:
            message = (
                f"#{head.name} preprocessing directive is not supported "
                "during concrete preprocessing"
            )
        else:
            message = "nonconditional preprocessing directive is not supported"
        context.diagnostics.append(
            context.located(ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, message, current_line)
        )
        return False


def _preprocess_unit(
    source: str,
    run: _Run,
    *,
    identity: str | None,
    filename: str | None,
    depth: int,
) -> _UnitOutput:
    """Preprocess one primary or included source against the shared macro state."""
    pragma_markers: tuple[_SourcePragma, ...] = ()
    if identity is not None and run.dispatch_pragmas:
        # Included pragmas become same-width markers that survive only on reachable
        # lines; the pragma layer dispatches them in output order. ``once`` is a
        # core include-management pragma and stays a directive.
        source, pragma_markers = _mask_source_pragmas(source, frozenset({"once"}))
    # Each __has_include invocation becomes a unique placeholder identifier that stays
    # unknown until a condition needing it is reached; it is then answered with the
    # macro state at that point.
    queries = _has_include.scan(source) if "__has_include" in source else ()
    if queries:
        source = _has_include.render(source, queries)
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    context = _UnitContext(
        source, run, identity=identity, filename=filename, depth=depth, queries=queries
    )
    _UnitProcessor(context, tree).run()
    if context.diagnostics:
        return _UnitOutput(tuple(context.diagnostics))
    return _finish_unit(context, pragma_markers)


def _finish_unit(context: _UnitContext, pragma_markers: tuple[_SourcePragma, ...]) -> _UnitOutput:
    """Render the output of a unit whose lines were all processed without diagnostics."""
    source = context.source
    identity = context.identity
    expansion = context.expansion
    retained = context.retained
    physical = context.physical
    output = "".join(
        line if keep else "".join(char if char in "\r\n" else " " for char in line)
        for line, keep in zip(physical, retained)
    )
    characters = list(output)
    char_kept = bytearray()
    for line, keep in zip(physical, retained):
        char_kept.extend(bytes([keep]) * len(line))
    for token in expansion.tokens:
        if (
            token.kind == "comment"
            and token.text.startswith("/*")
            and any(char_kept[token.start : token.end])
        ):
            characters[token.start : token.end] = source[token.start : token.end]
    restored = "".join(characters)
    restored_lines = split_lines(restored, keepends=True)
    removed_lines = frozenset(
        line_number
        for line_number, (line, keep) in enumerate(zip(restored_lines, retained), 1)
        if not keep and not line.rstrip("\r\n").strip()
    )
    output, source_map = expansion.render(restored)
    builtin = _unexpanded_predefined_macro(output, source_map, expansion)
    if builtin is not None:
        name, location = builtin
        return _UnitOutput(
            (
                PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                    f"predefined macro {name} is not supported during concrete preprocessing",
                    location,
                    identity,
                ),
            )
        )

    pragmas = []
    for marker in pragma_markers:
        output_offset = _output_offset_for_source(source_map, marker.marker_offset)
        if output_offset is None or output_offset >= len(output) or output[output_offset] != "0":
            continue
        assert identity is not None
        pragmas.append(
            _IncludedPragma(
                marker.payload,
                marker.location,
                identity,
                output_offset,
                marker.end_line - marker.start_line + 1,
            )
        )
    if identity is not None:
        source_map = tuple(replace(item, source_identity=identity) for item in source_map)
        if output and not _ends_line(output):
            # End of an included source ends its last line, as in compiler preprocessing.
            source_map += (_synthetic_newline(len(output), len(source), expansion, identity),)
            output += "\n"
    unit = _UnitOutput((), output, source_map, removed_lines, tuple(pragmas))
    if context.insertions:
        unit = _splice(unit, context.insertions, len(source), expansion, identity)
    return unit


def _unresolved_condition(
    directive: str,
    condition: str | None,
    environment: MacroLookup,
    line: int,
    identity: str | None,
    max_work: int,
) -> PreprocessDiagnostic:
    """Diagnose an undetermined condition with the names that would decide it."""
    unresolved = (
        _unresolved_macros(
            condition, environment, directive=directive, line=line, max_work=max_work
        )
        if condition is not None
        else ()
    )
    message = "condition is not determined by the current macro state"
    if condition is not None:
        message += f": #{directive} {condition}"
    if unresolved:
        names = ", ".join(
            f"{item.name} ({'/'.join(use.value for use in item.uses)})" for item in unresolved
        )
        message += f" (unresolved: {names})"
    return PreprocessDiagnostic(
        ErrorCode.UNRESOLVED_CONDITION,
        message,
        SourceLocation(line),
        identity,
        condition,
        unresolved,
    )


def _ends_line(text: str) -> bool:
    return text.endswith(("\n", "\r"))


def _synthetic_newline(
    output_offset: int,
    source_offset: int,
    expansion: Expansion,
    identity: str | None,
) -> SourceMapping:
    """Map a generated line ending to an empty range at the end of a source."""
    location = expansion.location(source_offset)
    return SourceMapping(
        output_offset,
        output_offset + 1,
        source_offset,
        source_offset,
        location,
        location,
        True,
        identity,
    )


def _splice(
    unit: _UnitOutput,
    insertions: list[tuple[int, int, _UnitOutput]],
    source_length: int,
    expansion: Expansion,
    identity: str | None,
) -> _UnitOutput:
    """Insert included outputs after their masked directive lines.

    Each insertion is ``(source_offset, directive_end_line, child)`` where
    ``source_offset`` is the start of the physical line after the directive.
    """
    output = unit.source
    mappings = list(unit.source_map)

    def output_offset(source_offset: int) -> int:
        if source_offset >= source_length:
            return len(output)
        for mapping in mappings:
            if mapping.source_start > source_offset or (
                mapping.expanded and mapping.source_start == source_offset
            ):
                return mapping.output_start
            if source_offset < mapping.source_end and not mapping.expanded:
                return mapping.output_start + source_offset - mapping.source_start
        return len(output)

    def split(at: int) -> None:
        for index, mapping in enumerate(mappings):
            if not (mapping.output_start < at < mapping.output_end) or mapping.expanded:
                continue
            middle = mapping.source_start + at - mapping.output_start
            location = expansion.location(middle)
            mappings[index : index + 1] = [
                replace(mapping, output_end=at, source_end=middle, end=location),
                replace(mapping, output_start=at, source_start=middle, start=location),
            ]
            return

    points = [(output_offset(offset), end_line, child) for offset, end_line, child in insertions]
    for at, _end_line, _child in points:
        split(at)

    pieces: list[str] = []
    combined: list[SourceMapping] = []
    removed: set[int] = set()
    pragmas: list[_IncludedPragma] = []
    char_shift = 0
    line_shift = 0
    cursor = 0
    pending = iter(sorted(unit.removed_lines))
    next_removed = next(pending, None)

    def own(until: int, through_line: int | None) -> None:
        nonlocal cursor, next_removed
        pieces.append(output[cursor:until])
        for mapping in mappings:
            if cursor <= mapping.output_start < until:
                combined.append(
                    replace(
                        mapping,
                        output_start=mapping.output_start + char_shift,
                        output_end=mapping.output_end + char_shift,
                    )
                )
        for pragma in unit.pragmas:
            if cursor <= pragma.output_start < until:
                pragmas.append(replace(pragma, output_start=pragma.output_start + char_shift))
        while next_removed is not None and (through_line is None or next_removed <= through_line):
            removed.add(next_removed + line_shift)
            next_removed = next(pending, None)
        cursor = until

    for at, end_line, child in points:
        own(at, end_line)
        if not child.source:
            continue
        if at == len(output) and output and not _ends_line(output):
            combined.append(_synthetic_newline(at + char_shift, source_length, expansion, identity))
            pieces.append("\n")
            char_shift += 1
        base = at + char_shift
        pieces.append(child.source)
        combined.extend(
            replace(
                mapping,
                output_start=mapping.output_start + base,
                output_end=mapping.output_end + base,
            )
            for mapping in child.source_map
        )
        pragmas.extend(
            replace(pragma, output_start=pragma.output_start + base) for pragma in child.pragmas
        )
        removed.update(line + end_line + line_shift for line in child.removed_lines)
        char_shift += len(child.source)
        line_shift += len(split_lines(child.source))
    own(len(output), None)
    return _UnitOutput(
        (),
        "".join(pieces),
        tuple(combined),
        frozenset(removed),
        tuple(pragmas),
    )


__all__ = [
    "PreprocessingContext",
    "PreprocessDiagnostic",
    "PreprocessResult",
    "SkippedInclude",
    "compact",
    "preprocess_source",
]
