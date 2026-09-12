"""Concrete conditional selection and macro expansion."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
from .expansion import Expansion, ExpansionError, SourceMapping
from .expressions import conjunction, expression_atoms_in_order, negate
from .model import ConditionError, ConditionalGroup, DefinedVariable, Variable, TRUE
from .macros import MacroEnvironment, MacroState, _apply_macro_directive
from .numeric_conditions import NumericConditionError, evaluate_numeric_condition
from .parser import logical_lines, parse_source
from .robdd import AnalysisBudget, AnalysisLimitExceeded, BDD


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
    options: AnalysisOptions | None = None,
) -> PreprocessResult:
    """Select conditional branches under an explicit concrete macro state.

    ``assumptions`` preserves the existing open-world Boolean contract: unmentioned
    macros remain unknown and assumed values do not provide replacement text.
    ``configuration`` instead seeds concrete external macro definitions and may
    opt into closed-world unknown-name handling. The two inputs are mutually
    exclusive so Boolean constraints cannot silently disagree with replacement
    definitions.

    Reachable integer expressions are macro expanded and evaluated concretely when
    Boolean reasoning alone cannot choose a branch. A still-undecidable condition
    returns an incomplete result with ``source=None``. Inactive text and
    conditional directives become spaces, preserving physical line endings.
    Unexpanded spans map one-to-one. Block comments overlapping retained text are
    kept whole to balance delimiters. Macros in retained text expand with invocation
    provenance in source_map. Active define/undef directives update macro state and
    override externally configured state in source order; they are masked in the
    output. Includes return an incomplete result. Successful results expose a
    detached, read-only final macro-state snapshot and immutable provenance for
    physical lines wholly removed by preprocessing. Malformed conditionals raise
    the same structured ParseError as analyze_source.
    """
    normalized = _normalize_assumptions(assumptions)
    if configuration is not None and normalized is not None:
        raise AnalysisError(
            "assumptions and configuration are mutually exclusive",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    environment = (
        _configured_environment(configuration)
        if configuration is not None
        else MacroEnvironment(normalized)
    )
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
    offsets = [0]
    for physical_line in physical:
        offsets.append(offsets[-1] + len(physical_line))
    current_line = None
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
            if re.match(r"^\s*#", line.text):
                expansion.flush(environment)
            branch = branches.get(current_line)
            if branch is not None:
                if current_line in starts:
                    stack.append([active, False, False])
                frame = stack[-1]
                active = False
                blank(current_line, ends[current_line])
                if frame[0] and not frame[1]:
                    terms = [semantics]
                    for name in names:
                        state = environment.get(name)
                        for atom, value in ((DefinedVariable(name), state.defined),
                                            (Variable(name), state.value)):
                            if value is not None:
                                terms.append(atom if value else negate(atom))
                    context = conjunction(*terms)
                    condition = branch.expression if branch.expression is not None else TRUE
                    if bdd.satisfiable(conjunction(context, condition)):
                        ambiguous = bdd.satisfiable(conjunction(context, negate(condition)))
                        selected: bool | None = None
                        if ambiguous and branch.expression_text is not None:
                            try:
                                selected = evaluate_numeric_condition(
                                    branch.expression_text,
                                    _condition_environment(environment),
                                    expansion,
                                    budget,
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
                frame[2] = active
                continue
            match = re.match(r"^\s*#\s*(endif|define|undef|include|include_next|import)\b(.*)$", line.text)
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
                try:
                    if kind not in {'define', 'undef'}:
                        raise ValueError('include processing is not supported')
                    concrete = expansion.directive_text(offsets[current_line - 1], offsets[ends[current_line]])
                    definition_match = re.fullmatch(r"#\s*(define|undef)\b(.*)", concrete, re.DOTALL)
                    if definition_match is None or definition_match[1] != kind:
                        raise ValueError('ambiguous directive after physical line splicing')
                    _apply_macro_directive(environment, kind, definition_match[2], SourceLocation(current_line))
                except ValueError as error:
                    diagnostics.append(PreprocessDiagnostic(
                        ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE, str(error),
                        SourceLocation(current_line),
                    ))
                    break
                blank(current_line, ends[current_line])
            elif not re.match(r"^\s*#", line.text):
                expansion.line(offsets[current_line - 1], offsets[ends[current_line]])
        if not diagnostics:
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
    return PreprocessResult(
        output,
        filename,
        macros=environment.snapshot(),
        source_map=source_map,
        removed_lines=removed_lines,
    )


__all__ = ["PreprocessDiagnostic", "PreprocessResult", "compact", "preprocess_source"]
