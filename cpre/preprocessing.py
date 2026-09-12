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
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expansion import Expansion, ExpansionError, SourceMapping
from .expressions import conjunction, expression_atoms_in_order, negate
from .model import ConditionError, ConditionalGroup, DefinedVariable, Variable, TRUE
from .macros import MacroEnvironment, MacroState, _apply_macro_directive
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

    @property
    def complete(self) -> bool:
        return self.source is not None and not self.incomplete


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    options: AnalysisOptions | None = None,
) -> PreprocessResult:
    """Select conditional branches under explicit Boolean macro assumptions.

    Unmentioned macros and opaque predicates remain unknown. A reachable
    undecidable condition returns an incomplete result with ``source=None``.
    Inactive text and conditional directives become spaces, preserving physical
    line endings. Unexpanded spans map one-to-one. Block comments overlapping
    retained text are kept whole to balance their delimiters. Macros in
    retained text expand with invocation provenance in source_map. Active
    define/undef directives update macro state and are masked; includes return
    an incomplete result.
    Successful results expose a detached, read-only final macro-state snapshot.
    Malformed conditionals raise the same structured ParseError as analyze_source.
    """
    normalized = _normalize_assumptions(assumptions)
    resolved_options = options if options is not None else AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError("options must be an AnalysisOptions instance",
                            code=ErrorCode.ANALYSIS_FAILURE)
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    environment = MacroEnvironment(normalized)
    physical = source.splitlines(keepends=True)
    logical = list(logical_lines(source))
    # The next logical start captures even empty final continuation lines.
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
        # Frames hold [parent-active, any-branch-selected, current-active].
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
                        if bdd.satisfiable(conjunction(context, negate(condition))):
                            diagnostics.append(PreprocessDiagnostic(
                                ErrorCode.UNRESOLVED_CONDITION,
                                "condition is not determined by the current macro state",
                                SourceLocation(current_line),
                            ))
                            break  # Subsequent state depends on this unknown choice.
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
    # A block comment can start on a removed directive and end beside retained
    # code (or vice versa). Restore only comments overlapping retained text so
    # delimiters stay balanced without leaking wholly discarded comments.
    # Quoted literals and line comments are skipped.
    characters = list(output)
    char_kept = bytearray()
    for line, keep in zip(physical, retained):
        char_kept.extend(bytes([keep]) * len(line))
    for token in expansion.tokens:
        if token.kind == "comment" and token.text.startswith("/*") and any(char_kept[token.start:token.end]):
            characters[token.start:token.end] = source[token.start:token.end]
    output, source_map = expansion.render("".join(characters))
    return PreprocessResult(output, filename, macros=environment.snapshot(), source_map=source_map)


__all__ = ["PreprocessDiagnostic", "PreprocessResult", "preprocess_source"]
