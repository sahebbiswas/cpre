"""Concrete conditional selection without macro expansion or include processing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .analysis import _macro_semantics, tree_expressions
from .api import (
    AnalysisIncomplete, AnalysisOptions, MacroAssumptions,
    _assumption_expression, _normalize_assumptions, _translate_parse_error,
)
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expressions import conjunction, expression_atoms_in_order, negate
from .model import ConditionError, ConditionalGroup, TRUE
from .parser import logical_lines, parse_source
from .robdd import AnalysisBudget, AnalysisLimitExceeded, BDD


@dataclass(frozen=True)
class PreprocessDiagnostic:
    """A reachable construct that prevents concrete conditional selection."""

    code: ErrorCode
    message: str
    location: SourceLocation


@dataclass(frozen=True)
class PreprocessResult:
    """Atomic selection result; incomplete results never expose partial source."""

    source: str | None
    filename: str | None = None
    incomplete: tuple[PreprocessDiagnostic | AnalysisIncomplete, ...] = ()

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
    line endings and columns. Block comments overlapping retained text are kept
    whole to balance their delimiters. Retained text is unchanged. No macro
    expansion or include processing is performed; reachable define/undef/include directives
    return an incomplete result because they may change subsequent macro state.
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
    current_line = None
    try:
        context = conjunction(_assumption_expression(normalized) if normalized else TRUE,
                              _macro_semantics(tree, legacy_symbolic=False))
        atoms = [atom for expression in (*tree_expressions(tree.groups), context)
                 for atom in expression_atoms_in_order(expression)]
        bdd = BDD(atoms, limits=limits, budget=AnalysisBudget(limits.max_work))

        def select(groups: list[ConditionalGroup]) -> None:
            nonlocal current_line
            for group in groups:
                assert group.end_line is not None
                selected = False
                unresolved = False
                for index, branch in enumerate(group.branches):
                    current_line = branch.line
                    end = (group.branches[index + 1].line - 1
                           if index + 1 < len(group.branches) else group.end_line - 1)
                    blank(branch.line, ends[branch.line])
                    if selected or unresolved:
                        blank(branch.line, end)
                        continue
                    condition = branch.expression if branch.expression is not None else TRUE
                    if not bdd.satisfiable(conjunction(context, condition)):
                        blank(branch.line, end)
                    elif bdd.satisfiable(conjunction(context, negate(condition))):
                        diagnostics.append(PreprocessDiagnostic(
                            ErrorCode.UNRESOLVED_CONDITION,
                            "condition is not determined by the supplied macro assumptions",
                            SourceLocation(branch.line),
                        ))
                        unresolved = True
                        blank(branch.line, end)
                    else:
                        selected = True
                        select(branch.children)
                blank(group.end_line, ends[group.end_line])

        select(tree.groups)
    except AnalysisLimitExceeded as error:
        diagnostics.append(AnalysisIncomplete(
            ErrorCode.ANALYSIS_LIMIT_EXCEEDED, error.resource, error.limit,
            error.observed, str(error),
            SourceLocation(current_line) if current_line is not None else None,
        ))

    for line in logical:
        if retained[line.start_line - 1] and re.match(
            r"^\s*#\s*(?:define|undef|include|include_next|import)\b", line.text
        ):
            diagnostics.append(PreprocessDiagnostic(
                ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE,
                "macro-state directives and includes require preprocessing beyond conditional selection",
                SourceLocation(line.start_line),
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
    tokens = re.finditer(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\r\n]*|/\*.*?\*/',
        source, re.DOTALL,
    )
    characters = list(output)
    char_kept = bytearray()
    for line, keep in zip(physical, retained):
        char_kept.extend(bytes([keep]) * len(line))
    for token in tokens:
        if token.group().startswith("/*") and any(char_kept[token.start():token.end()]):
            characters[token.start():token.end()] = token.group()
    return PreprocessResult("".join(characters), filename)


__all__ = ["PreprocessDiagnostic", "PreprocessResult", "preprocess_source"]
