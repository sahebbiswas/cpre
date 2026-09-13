"""Concrete conditional selection and macro expansion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .analysis import _macro_semantics, tree_expressions
from .api import (
    AnalysisIncomplete, AnalysisOptions, MacroAssumptions,
    _normalize_assumptions, _translate_parse_error,
)
from .configuration import (
    MacroConfiguration, _condition_environment, _configured_environment,
)
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, ExpansionError, SourceMapping, Token, tokenize
from .expressions import conjunction, expression_atoms_in_order, negate
from .model import ConditionError, ConditionalGroup, DefinedVariable, Variable, TRUE
from .macros import MacroDefinition, MacroEnvironment, MacroState, _apply_macro_directive
from .numeric_conditions import NumericConditionError, evaluate_numeric_condition
from .parser import logical_lines, parse_source
from .robdd import AnalysisBudget, AnalysisLimitExceeded, BDD


# These names have implementation-provided semantics in common C/C++ preprocessors.
# cpre must never silently certify them as ordinary identifiers. Explicit concrete
# definitions may model environment macros such as __STDC__; otherwise a reachable
# value use is reported as unsupported until cpre implements deterministic semantics.
_PREDEFINED_MACROS = frozenset({
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
})
_STANDARD_CONFIGURABLE_PREDEFINED = frozenset({
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
})
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
    """A reachable construct that prevents supported concrete preprocessing."""

    code: ErrorCode
    message: str
    location: SourceLocation


@dataclass(frozen=True)
class PreprocessResult:
    """Atomic selection result; incomplete results never expose partial source."""

    source: str | None
    filename: str | None = None
    incomplete: tuple[PreprocessDiagnostic | AnalysisIncomplete, ...] = ()

    macros: Mapping[str, MacroState] | None = None
    source_map: tuple[SourceMapping, ...] | None = None
    removed_lines: frozenset[int] | None = None

    @property
    def complete(self) -> bool:
        return self.source is not None and not self.incomplete


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
            raise ValueError('#line filename must expand to an ordinary string literal')
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
    return atomic incomplete results. Definedness-only checks remain ordinary
    conditional reasoning. Successful results expose a detached, read-only final
    macro-state snapshot and immutable provenance for physical lines wholly removed
    by preprocessing. Malformed conditionals raise the same structured ParseError
    as analyze_source.
    """
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
        raise AnalysisError("options must be an AnalysisOptions instance",
                            code=ErrorCode.ANALYSIS_FAILURE)
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    physical = source.splitlines(keepends=True)
    logical = list(logical_lines(source))
    ends = {line.start_line: (logical[index + 1].start_line - 1
                             if index + 1 < len(logical) else len(physical))
            for index, line in enumerate(logical)}
    retained = [True] * len(physical)
    diagnostics: list[PreprocessDiagnostic | AnalysisIncomplete] = []

    def blank(start: int, end: int) -> None:
        retained[start - 1:end] = [False] * (end - start + 1)

    limits = resolved_options._resource_limits()
    budget = AnalysisBudget(limits.max_work)
    expansion = Expansion(source, budget)
    logical_states: dict[int, _LogicalPreprocessingState] = {}
    environment = _PredefinedMacroEnvironment(base_environment, expansion, logical_states)
    offsets = [0]
    for physical_line in physical:
        offsets.append(offsets[-1] + len(physical_line))
    current_line = None
    logical_delta = 0
    logical_file_literal = _file_literal(filename)
    try:
        semantics = _macro_semantics(tree, legacy_symbolic=False)
        atoms = [atom for expression in (*tree_expressions(tree.groups), semantics)
                 for atom in expression_atoms_in_order(expression)]
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
                        builtin = (
                            _unconfigured_predefined_macro(branch.expression_text, environment)
                            if branch.expression_text is not None
                            and branch.directive not in _DEFINEDNESS_DIRECTIVES
                            else None
                        )
                        if builtin is not None:
                            diagnostics.append(PreprocessDiagnostic(
                                ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                                f"predefined macro {builtin} is not supported during concrete preprocessing",
                                SourceLocation(current_line),
                            ))
                            break
                        terms = [semantics]
                        for name in names:
                            state = environment.get(name)
                            for atom, value in ((DefinedVariable(name), state.defined),
                                                (Variable(name), state.value)):
                                if value is not None:
                                    terms.append(atom if value else negate(atom))
                        context_expression = conjunction(*terms)
                        condition = branch.expression if branch.expression is not None else TRUE
                        if bdd.satisfiable(conjunction(context_expression, condition)):
                            ambiguous = bdd.satisfiable(conjunction(context_expression, negate(condition)))
                            selected: bool | None = None
                            if (ambiguous and branch.expression_text is not None
                                    and branch.directive not in _DEFINEDNESS_DIRECTIVES):
                                try:
                                    selected = evaluate_numeric_condition(
                                        branch.expression_text,
                                        _condition_environment(environment),
                                        expansion,
                                        budget,
                                        unsupported_identifiers=_PREDEFINED_MACROS,
                                    )
                                except NumericConditionError as error:
                                    diagnostics.append(PreprocessDiagnostic(
                                        ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION,
                                        str(error), SourceLocation(current_line),
                                    ))
                                    break
                                except ExpansionError as error:
                                    diagnostics.append(PreprocessDiagnostic(
                                        ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
                                        str(error), SourceLocation(current_line),
                                    ))
                                    break
                            if ambiguous and selected is None:
                                diagnostics.append(PreprocessDiagnostic(
                                    ErrorCode.UNRESOLVED_CONDITION,
                                    "condition is not determined by the current macro state",
                                    SourceLocation(current_line),
                                ))
                                break
                            if not ambiguous or selected:
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
            if match and match[1] == 'endif':
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
                    if kind == 'line':
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
                    if kind not in {'define', 'undef'}:
                        raise ValueError('include processing is not supported')
                    definition_match = re.fullmatch(r"#\s*(define|undef)\b(.*)", concrete, re.DOTALL)
                    if definition_match is None or definition_match[1] != kind:
                        raise ValueError('ambiguous directive after physical line splicing')
                    remainder = definition_match[2]
                    _apply_macro_directive(environment, kind, remainder, SourceLocation(current_line))
                    if kind == 'define':
                        name_match = re.match(r"\s*([A-Za-z_]\w*)", remainder)
                        assert name_match is not None
                        definition = environment.get(name_match[1]).definition
                except ValueError as error:
                    diagnostics.append(PreprocessDiagnostic(
                        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, str(error),
                        SourceLocation(current_line),
                    ))
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
                directive_match = re.match(r"#\s*([A-Za-z_]\w*)\b", concrete)
                if directive_match is not None:
                    message = (
                        f"#{directive_match[1]} preprocessing directive is not supported "
                        "during concrete preprocessing"
                    )
                else:
                    message = "nonconditional preprocessing directive is not supported"
                diagnostics.append(PreprocessDiagnostic(
                    ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                    message,
                    SourceLocation(current_line),
                ))
                break
            else:
                environment.logical_override = None
                expansion.line(offsets[current_line - 1], offsets[ends[current_line]])
        if not diagnostics:
            environment.logical_override = None
            expansion.flush(environment)
    except ExpansionError as error:
        diagnostics.append(PreprocessDiagnostic(
            ErrorCode.UNSUPPORTED_MACRO_EXPANSION, str(error),
            expansion.location(expansion.current_offset),
        ))
    except AnalysisLimitExceeded as error:
        limit_line = error.line if error.line is not None else current_line
        diagnostics.append(AnalysisIncomplete(
            ErrorCode.ANALYSIS_LIMIT_EXCEEDED, error.resource, error.limit,
            error.observed, str(error),
            SourceLocation(limit_line) if limit_line is not None else None,
        ))

    if diagnostics:
        diagnostics.sort(key=lambda item: item.location.line if item.location else 0)
        return PreprocessResult(None, filename, tuple(diagnostics))
    output = "".join(line if keep else "".join(
        char if char in "\r\n" else " " for char in line
    ) for line, keep in zip(physical, retained))
    characters = list(output)
    char_kept = bytearray()
    for line, keep in zip(physical, retained):
        char_kept.extend(bytes([keep]) * len(line))
    for token in expansion.tokens:
        if token.kind == "comment" and token.text.startswith("/*") and any(char_kept[token.start:token.end]):
            characters[token.start:token.end] = source[token.start:token.end]
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
        return PreprocessResult(None, filename, (PreprocessDiagnostic(
            ErrorCode.UNSUPPORTED_MACRO_EXPANSION,
            f"predefined macro {name} is not supported during concrete preprocessing",
            location,
        ),))
    return PreprocessResult(
        output,
        filename,
        macros=environment.snapshot(),
        source_map=source_map,
        removed_lines=removed_lines,
    )


__all__ = [
    "PreprocessingContext",
    "PreprocessDiagnostic",
    "PreprocessResult",
    "compact",
    "preprocess_source",
]
