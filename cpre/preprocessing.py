"""Concrete conditional selection and macro expansion."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from . import _has_include
from ._pragma_syntax import _mask_source_pragmas, _output_offset_for_source, _SourcePragma
from .analysis import _macro_semantics, tree_expressions
from .api import (
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
from .expansion import Expansion, ExpansionError, SourceMapping, Token, tokenize
from .expressions import conjunction, expression_atoms_in_order, negate
from .includes import (
    DEFAULT_MAX_INCLUDE_DEPTH,
    IncludeForm,
    IncludeOutcome,
    IncludeRecord,
    IncludeRequest,
    IncludeResolver,
    ResolvedInclude,
)
from .macros import MacroDefinition, MacroEnvironment, MacroState, _apply_macro_directive
from .model import (
    TRUE,
    ConditionalBranch,
    ConditionalGroup,
    ConditionError,
    DefinedVariable,
    Variable,
)
from .numeric_conditions import NumericConditionError, evaluate_numeric_condition
from .parser import logical_lines, parse_source
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
    match = re.fullmatch(r"#\s*line\b(.*)", concrete, re.DOTALL)
    if match is None:
        raise ValueError("malformed #line directive")
    tokens = _line_directive_operands(match[1], environment, state, budget)
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


def _unconfigured_predefined_macro(text: str, environment: MacroEnvironment) -> str | None:
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


_INCLUDE_DIRECTIVE_RE = re.compile(r"#\s*(include_next|include|import)\b(.*)", re.DOTALL)


def _include_directive(concrete: str) -> tuple[str, str] | None:
    """Return ``(kind, operand)`` for a spliced include-family directive."""
    match = _INCLUDE_DIRECTIVE_RE.fullmatch(concrete)
    if match is None:
        return None
    return match[1], match[2].strip()


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
    spliced = re.sub(r"\\(?:\r\n|\r|\n)", "", raw).rstrip("\r\n")
    prefix = re.match(r"\s*#\s*(?:include_next|include|import)\b[ \t\f\v]*", spliced)
    if prefix is not None:
        rest = spliced[prefix.end() :]
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
    for line_number, line in enumerate(result.source.splitlines(keepends=True), 1):
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


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    configuration: MacroConfiguration | None = None,
    context: PreprocessingContext | None = None,
    options: AnalysisOptions | None = None,
    skip_includes: bool = False,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
    _dispatch_included_pragmas: bool = False,
    _include_query: _has_include.IncludeQueryProvider | None = None,
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
    """
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
        _dispatch_included_pragmas,
        _include_query,
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
    queries_by_line: dict[int, list[_has_include.Occurrence]] = {}
    for occurrence in queries:
        queries_by_line.setdefault(occurrence.directive_line, []).append(occurrence)
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    base_environment = run.base_environment
    limits = run.limits
    budget = run.budget
    includes = run.includes
    physical = source.splitlines(keepends=True)
    logical = list(logical_lines(source))
    ends = {
        line.start_line: (
            logical[index + 1].start_line - 1 if index + 1 < len(logical) else len(physical)
        )
        for index, line in enumerate(logical)
    }
    retained = [True] * len(physical)
    diagnostics: list[PreprocessDiagnostic | AnalysisIncomplete] = []
    insertions: list[tuple[int, int, _UnitOutput]] = []

    def blank(start: int, end: int) -> None:
        retained[start - 1 : end] = [False] * (end - start + 1)

    def located(code: ErrorCode, message: str, line: int) -> PreprocessDiagnostic:
        return PreprocessDiagnostic(code, message, SourceLocation(line), identity)

    expansion = Expansion(source, budget)
    logical_states: dict[int, _LogicalPreprocessingState] = {}
    environment = _PredefinedMacroEnvironment(base_environment, expansion, logical_states)
    view = _has_include.ConditionView(environment, queries, run.include_query is not None)
    offsets = [0]
    for physical_line in physical:
        offsets.append(offsets[-1] + len(physical_line))
    current_line = None
    logical_delta = 0
    logical_file_literal = _file_literal(filename)

    def skip(kind: str, operand: str, location: SourceLocation) -> None:
        run.skipped.append(SkippedInclude(kind, operand, location, identity))
        if isinstance(base_environment, _ConfiguredMacroEnvironment):
            # Names the caller configured or the source already settled keep their
            # state; any other name may come from the unread header.
            base_environment._skip_include()

    def has_include_diagnostic(
        code: ErrorCode, message: str, occurrence: _has_include.Occurrence
    ) -> None:
        diagnostics.append(PreprocessDiagnostic(code, message, occurrence.location, identity))

    def answer_query(occurrence: _has_include.Occurrence) -> bool:
        """Answer one reachable __has_include; False means diagnostics were added."""
        if occurrence.operator == _has_include.HAS_INCLUDE_NEXT:
            has_include_diagnostic(
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                "__has_include_next is not supported during concrete preprocessing",
                occurrence,
            )
            return False
        if occurrence.operand is None:
            has_include_diagnostic(
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                "__has_include expects one parenthesized header-name operand",
                occurrence,
            )
            return False
        if run.include_query is None:
            has_include_diagnostic(
                ErrorCode.UNRESOLVED_CONDITION,
                "__has_include requires caller-provided include availability",
                occurrence,
            )
            return False
        try:
            query = _has_include.header_query(occurrence, environment, budget, filename)
        except _has_include.HeaderOperandError as error:
            has_include_diagnostic(
                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION, str(error), occurrence
            )
            return False
        except ExpansionError as error:
            has_include_diagnostic(ErrorCode.UNSUPPORTED_MACRO_EXPANSION, str(error), occurrence)
            return False
        except AnalysisLimitExceeded as error:
            diagnostics.append(
                AnalysisIncomplete(
                    ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
                    error.resource,
                    error.limit,
                    error.observed,
                    str(error),
                    occurrence.location,
                    source_identity=identity,
                )
            )
            return False
        available = run.include_query(query)
        if available is None:
            delimiter = (
                f'"{query.header}"' if query.form is IncludeForm.QUOTED else f"<{query.header}>"
            )
            has_include_diagnostic(
                ErrorCode.UNRESOLVED_CONDITION,
                f"include availability is unknown for {delimiter}",
                occurrence,
            )
            return False
        if type(available) is not bool:
            raise AnalysisError(
                "include_query must return True, False, or None",
                code=ErrorCode.INVALID_CONFIGURATION,
                location=occurrence.location,
                filename=filename,
            )
        view.answers[occurrence.placeholder] = available
        return True

    def select_branch(branch: ConditionalBranch) -> bool | None:
        """Decide one reachable branch; None means diagnostics were added.

        A condition left undetermined by an unanswered ``__has_include`` asks for
        the first such query on the line and is re-evaluated, so queries in
        irrelevant Boolean terms are never sent.
        """
        line_queries = queries_by_line.get(branch.line, [])
        builtin = (
            _unconfigured_predefined_macro(branch.expression_text, view)
            if branch.expression_text is not None
            and branch.directive not in _DEFINEDNESS_DIRECTIVES
            else None
        )
        if builtin is not None:
            diagnostics.append(
                located(
                    ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                    f"predefined macro {builtin} is not supported during concrete preprocessing",
                    branch.line,
                )
            )
            return None
        condition = branch.expression if branch.expression is not None else TRUE
        while True:
            terms = [semantics]
            for name in names:
                macro = view.get(name)
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
            pending = next(
                (item for item in line_queries if item.placeholder not in view.answers), None
            )
            if branch.expression_text is not None and branch.directive not in (
                _DEFINEDNESS_DIRECTIVES
            ):
                try:
                    selected = evaluate_numeric_condition(
                        branch.expression_text,
                        _condition_environment(view),  # type: ignore[arg-type]
                        expansion,
                        budget,
                        unsupported_identifiers=_PREDEFINED_MACROS,
                    )
                except NumericConditionError as error:
                    if pending is not None:
                        selected = None
                    else:
                        diagnostics.append(
                            located(
                                ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                                str(error),
                                branch.line,
                            )
                        )
                        return None
                except ExpansionError as error:
                    diagnostics.append(
                        located(ErrorCode.UNSUPPORTED_MACRO_EXPANSION, str(error), branch.line)
                    )
                    return None
                if selected is not None:
                    return selected
            if pending is not None:
                if not answer_query(pending):
                    return None
                continue
            condition_text = (
                _has_include.restore(branch.expression_text, line_queries)
                if branch.expression_text is not None
                else None
            )
            diagnostics.append(
                _unresolved_condition(
                    branch.directive,
                    condition_text,
                    view,  # type: ignore[arg-type]
                    branch.line,
                    identity,
                    limits.max_work,
                )
            )
            return None

    def include_line(kind: str, operand: str, line: int, state: _LogicalPreprocessingState) -> bool:
        """Handle one reachable include directive; False means diagnostics were added."""
        location = SourceLocation(line)
        if not operand:
            diagnostics.append(
                located(
                    ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                    f"#{kind} expects a header name",
                    line,
                )
            )
            return False
        if includes is None:
            skip(kind, operand, location)
            blank(line, ends[line])
            return True

        expansion.current_offset = offsets[line - 1]
        try:
            header, form = _include_header(
                source[offsets[line - 1] : offsets[ends[line]]],
                operand,
                environment,
                state,
                budget,
            )
        except ValueError as error:
            diagnostics.append(
                located(ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, str(error), line)
            )
            return False
        spelling = f'"{header}"' if form is IncludeForm.QUOTED else f"<{header}>"
        includer = identity if identity is not None else filename
        request = IncludeRequest(header, form, kind, location, includer, depth)
        resolved = includes.resolver(request)
        if resolved is None:
            if run.skip_includes:
                skip(kind, operand, location)
                blank(line, ends[line])
                return True
            diagnostics.append(
                located(
                    ErrorCode.UNRESOLVED_INCLUDE,
                    f"cannot resolve #{kind} {spelling}",
                    line,
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
        outcome: IncludeOutcome | None = None
        if target in includes.once:
            outcome = IncludeOutcome.PRAGMA_ONCE
        elif target in includes.entered and (kind == "import" or target in includes.imported):
            # #import makes a source include-once, as in GCC and Clang.
            outcome = IncludeOutcome.IMPORTED
        else:
            if target not in includes.guards:
                includes.guards[target] = _detect_include_guard(resolved.source)
            guard = includes.guards[target]
            if guard is not None and environment.get(guard).defined is True:
                outcome = IncludeOutcome.GUARDED
        if kind == "import":
            includes.imported.add(target)
        if outcome is not None:
            includes.records.append(IncludeRecord(request, target, outcome))
            blank(line, ends[line])
            return True

        if target in includes.stack:
            diagnostics.append(
                located(
                    ErrorCode.INCLUDE_CYCLE,
                    f"#{kind} {spelling} re-enters {target}, which is already being preprocessed",
                    line,
                )
            )
            return False
        if depth + 1 > includes.max_depth:
            diagnostics.append(
                located(
                    ErrorCode.INCLUDE_DEPTH_EXCEEDED,
                    f"#{kind} {spelling} exceeds the maximum include depth of {includes.max_depth}",
                    line,
                )
            )
            return False

        guard = includes.guards[target]
        if guard is not None and environment.get(guard).defined is None:
            # A detected include guard is file-private, as in
            # MacroConfiguration.from_source(): unless the caller configured it,
            # it starts undefined so the guarded body is entered.
            environment.undef(guard)
        includes.records.append(IncludeRecord(request, target, IncludeOutcome.ENTERED))
        includes.entered.add(target)
        includes.stack.append(target)
        try:
            child = _preprocess_unit(
                resolved.source,
                run,
                identity=target,
                filename=target,
                depth=depth + 1,
            )
        finally:
            includes.stack.pop()
        # Names defined or undefined by the included source are explicit state now.
        environment._explicit_names.update(base_environment.snapshot())
        if child.diagnostics:
            diagnostics.extend(child.diagnostics)
            return False
        blank(line, ends[line])
        insertions.append((offsets[ends[line]], ends[line], child))
        return True

    try:
        semantics = _macro_semantics(tree, legacy_symbolic=False)
        atoms = [
            atom
            for expression in (*tree_expressions(tree.groups), semantics)
            for atom in expression_atoms_in_order(expression)
        ]
        bdd = BDD(atoms, limits=limits, budget=budget)
        names = sorted({atom.name for atom in atoms if isinstance(atom, Variable)})
        starts = {group.line: group for group in tree.groups}

        def index_groups(groups: list[ConditionalGroup]) -> None:
            for group in groups:
                starts[group.line] = group
                for branch in group.branches:
                    index_groups(branch.children)

        index_groups(tree.groups)
        branches = {branch.line: branch for group in starts.values() for branch in group.branches}
        stack: list[list[bool]] = []
        active = True
        for line in logical:
            current_line = line.start_line
            for physical_line_number in range(current_line, ends[current_line] + 1):
                logical_states[physical_line_number] = _LogicalPreprocessingState(
                    physical_line_number + logical_delta,
                    logical_file_literal,
                )
            line_state = logical_states[current_line]
            is_directive = re.match(r"^\s*#", line.text) is not None
            if is_directive:
                environment.logical_override = None
                expansion.flush(environment)
            branch = branches.get(current_line)
            if branch is not None:
                if current_line in starts:
                    stack.append([active, False, False])
                frame = stack[-1]
                active = False
                blank(current_line, ends[current_line])
                environment.logical_override = line_state
                try:
                    if frame[0] and not frame[1]:
                        selected = select_branch(branch)
                        if selected is None:
                            break
                        if selected:
                            active = True
                            frame[1] = True
                finally:
                    environment.logical_override = None
                frame[2] = active
                continue
            match = re.match(
                r"^\s*#\s*(endif|define|undef|line|include|include_next|import)\b(.*)$",
                line.text,
            )
            if match and match[1] == "endif":
                stack.pop()
                active = stack[-1][2] if stack else True
                blank(current_line, ends[current_line])
                continue
            if not active:
                blank(current_line, ends[current_line])
                continue
            if match:
                kind = match[1]
                definition = None
                try:
                    concrete = expansion.directive_text(
                        offsets[current_line - 1], offsets[ends[current_line]]
                    )
                    if kind == "line":
                        expansion.current_offset = offsets[current_line - 1]
                        next_line, next_file_literal = _parse_line_directive(
                            concrete,
                            environment,
                            line_state,
                            budget,
                        )
                        logical_delta = next_line - (ends[current_line] + 1)
                        if next_file_literal is not None:
                            logical_file_literal = next_file_literal
                        blank(current_line, ends[current_line])
                        continue
                    if kind not in {"define", "undef"}:
                        if not run.skip_includes and includes is None:
                            raise ValueError("include processing is not supported")
                        directive = _include_directive(concrete)
                        if directive is None or directive[0] != kind:
                            raise ValueError("ambiguous directive after physical line splicing")
                    else:
                        definition_match = re.fullmatch(
                            r"#\s*(define|undef)\b(.*)", concrete, re.DOTALL
                        )
                        if definition_match is None or definition_match[1] != kind:
                            raise ValueError("ambiguous directive after physical line splicing")
                        remainder = definition_match[2]
                        _apply_macro_directive(
                            environment, kind, remainder, SourceLocation(current_line)
                        )
                        if kind == "define":
                            name_match = re.match(r"\s*([A-Za-z_]\w*)", remainder)
                            assert name_match is not None
                            definition = environment.get(name_match[1]).definition
                except ValueError as error:
                    diagnostics.append(
                        located(
                            ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                            str(error),
                            current_line,
                        )
                    )
                    break
                if kind not in {"define", "undef"}:
                    if include_line(kind, directive[1], current_line, line_state):
                        continue
                    break
                if definition is not None:
                    expansion.current_offset = offsets[current_line - 1]
                    expansion.validate_definition(definition)
                blank(current_line, ends[current_line])
            elif is_directive:
                concrete = expansion.directive_text(
                    offsets[current_line - 1], offsets[ends[current_line]]
                )
                if re.fullmatch(r"#\s*", concrete, re.DOTALL):
                    blank(current_line, ends[current_line])
                    continue
                if identity is not None and includes is not None:
                    if re.fullmatch(r"#\s*pragma\s+once", concrete):
                        includes.once.add(identity)
                        blank(current_line, ends[current_line])
                        continue
                if run.skip_includes or includes is not None:
                    directive = _include_directive(concrete)
                    if directive is not None:
                        if include_line(directive[0], directive[1], current_line, line_state):
                            continue
                        break
                directive_match = re.match(r"#\s*([A-Za-z_]\w*)\b", concrete)
                if directive_match is not None:
                    message = (
                        f"#{directive_match[1]} preprocessing directive is not supported "
                        "during concrete preprocessing"
                    )
                else:
                    message = "nonconditional preprocessing directive is not supported"
                diagnostics.append(
                    located(
                        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                        message,
                        current_line,
                    )
                )
                break
            else:
                environment.logical_override = None
                expansion.line(offsets[current_line - 1], offsets[ends[current_line]])
        if not diagnostics:
            environment.logical_override = None
            expansion.flush(environment)
    except ExpansionError as error:
        diagnostics.append(
            PreprocessDiagnostic(
                ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                str(error),
                expansion.location(expansion.current_offset),
                identity,
            )
        )
    except AnalysisLimitExceeded as error:
        limit_line = error.line if error.line is not None else current_line
        diagnostics.append(
            AnalysisIncomplete(
                ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
                error.resource,
                error.limit,
                error.observed,
                str(error),
                SourceLocation(limit_line) if limit_line is not None else None,
                source_identity=identity,
            )
        )

    if diagnostics:
        return _UnitOutput(tuple(diagnostics))
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
    restored_lines = restored.splitlines(keepends=True)
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
    if insertions:
        unit = _splice(unit, insertions, len(source), expansion, identity)
    return unit


def _unresolved_condition(
    directive: str,
    condition: str | None,
    environment: MacroEnvironment,
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
        line_shift += len(child.source.splitlines())
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
