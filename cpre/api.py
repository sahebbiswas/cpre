"""Stable programmatic API for cpre library consumers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from . import cpre as _engine
from .errors import AnalysisError, CpreError, ErrorCode, ParseError, SourceLocation


class FindingKind(str, Enum):
    """Machine-readable categories emitted by :func:`analyze_source`."""

    DEAD_BRANCH = "dead_branch"
    REDUNDANT_BRANCH = "redundant_branch"
    SIMPLIFIABLE_CONDITION = "simplifiable_condition"
    CONTEXTUAL_SIMPLIFICATION = "contextual_simplification"


class FixConfidence(str, Enum):
    """Equivalence guarantee attached to a structured source edit."""

    EXACT = "exact"
    CONTEXTUAL = "contextual"


@dataclass(frozen=True)
class SourceRange:
    """Physical one-based source range with an exclusive end location."""

    start: SourceLocation
    end: SourceLocation


@dataclass(frozen=True)
class SuggestedEdit:
    """A direct replacement for one preprocessor condition source range."""

    range: SourceRange
    replacement: str
    confidence: FixConfidence


@dataclass(frozen=True)
class ExactSimplification:
    """A globally equivalent replacement for a source condition.

    ``replacement`` is Boolean-equivalent to ``original`` for every assignment
    of the condition's modeled predicates. It is therefore suitable for
    consumers that require a context-independent mechanical replacement.

    Constant replacements use the canonical strings ``"0"`` and ``"1"``.
    Instances are only returned when the replacement is semantically different
    from the source condition; an unchanged condition is represented by
    ``None`` on :class:`Finding`.
    """

    original: str
    replacement: str


@dataclass(frozen=True)
class ContextualSimplification:
    """A replacement equivalent only in the branch's reachable context.

    ``replacement`` may rely on enclosing conditions and preceding branches,
    so it must not be treated as globally equivalent to ``original``. Constant
    replacements use the canonical strings ``"0"`` and ``"1"``.

    Instances are only returned when contextual reasoning improves on the exact
    simplification (or on the original expression when no exact simplification
    exists). Otherwise the corresponding :class:`Finding` field is ``None``.
    """

    original: str
    replacement: str


@dataclass(frozen=True)
class Finding:
    """A structured preprocessor-analysis finding.

    ``edit`` is present only when cpre can identify the complete condition
    expression range and can represent the simplification as a direct
    replacement. Exact and contextual edits carry distinct confidence values.
    Dead/redundant branch diagnostics deliberately do not imply branch deletion.
    """

    kind: FindingKind
    location: SourceLocation
    directive: str
    original_condition: str | None
    exact_simplification: ExactSimplification | None
    contextual_simplification: ContextualSimplification | None
    reason: str
    opaque_predicates: tuple[str, ...] = ()
    edit: SuggestedEdit | None = None

    @property
    def simplified_condition(self) -> str | None:
        """Backward-compatible exact replacement string, if one exists."""

        if self.exact_simplification is None:
            return None
        return self.exact_simplification.replacement

    @property
    def contextual_condition(self) -> str | None:
        """Backward-compatible contextual replacement string, if one exists."""

        if self.contextual_simplification is None:
            return None
        return self.contextual_simplification.replacement


@dataclass(frozen=True)
class AnalysisResult:
    """Structured result returned by :func:`analyze_source`."""

    findings: tuple[Finding, ...]
    tree: _engine.ConditionalTree
    filename: str | None = None


def _branches(
    groups: list[_engine.ConditionalGroup],
) -> Iterator[_engine.ConditionalBranch]:
    for group in groups:
        for branch in group.branches:
            yield branch
            yield from _branches(branch.children)


def _formatted(expression: _engine.Expression | None) -> str | None:
    if expression is None:
        return None
    return _engine.format_expression(expression)


def _globally_equivalent(
    left: _engine.Expression,
    right: _engine.Expression,
) -> bool:
    """Return whether two expressions are equivalent without branch context."""

    atoms = [
        atom
        for expression in (left, right)
        for atom in _engine._expression_atoms_in_order(expression)
    ]
    bdd = _engine._BDD(atoms)
    return bdd.equivalent_under(_engine.TRUE, left, right)


def _condition_ranges(source: str) -> dict[tuple[int, str], SourceRange]:
    """Map editable #if/#elif conditions to their physical source ranges."""

    result: dict[tuple[int, str], SourceRange] = {}
    for logical_line in _engine._logical_lines(source):
        match = _engine._DIRECTIVE_RE.match(logical_line.text)
        if match is None:
            continue
        kind, remainder = match.group(1), match.group(2)
        if kind not in {"if", "elif"}:
            continue

        start_offset = len(remainder) - len(remainder.lstrip())
        end_offset = len(remainder.rstrip())
        if end_offset <= start_offset:
            continue

        locations = logical_line.locations[match.start(2) :]
        start = locations[start_offset]
        last = locations[end_offset - 1]
        if start.line is None or last.line is None:
            continue
        result[(logical_line.start_line, kind)] = SourceRange(
            start=SourceLocation(start.line, start.column),
            end=SourceLocation(last.line, last.column + 1),
        )
    return result


def _edit_for(
    branch: _engine.ConditionalBranch,
    ranges: dict[tuple[int, str], SourceRange],
    replacement: str,
    confidence: FixConfidence,
) -> SuggestedEdit | None:
    if branch.directive not in {"if", "elif"}:
        return None
    source_range = ranges.get((branch.line, branch.directive))
    if source_range is None:
        return None
    return SuggestedEdit(
        range=source_range,
        replacement=replacement,
        confidence=confidence,
    )


def _simplifications_for_branch(
    branch: _engine.ConditionalBranch,
) -> tuple[ExactSimplification | None, ContextualSimplification | None]:
    analysis = branch.analysis
    assert analysis is not None

    if branch.expression is None or branch.expression_text is None:
        return None, None

    original = branch.expression_text
    exact: ExactSimplification | None = None
    if analysis.simplified is not None and _engine._expressions_differ(
        branch.expression, analysis.simplified
    ):
        replacement = _formatted(analysis.simplified)
        assert replacement is not None
        exact = ExactSimplification(original=original, replacement=replacement)

    comparison = analysis.simplified or branch.expression
    contextual: ContextualSimplification | None = None
    if (
        analysis.contextual is not None
        and _engine._expressions_differ(comparison, analysis.contextual)
        and not _globally_equivalent(comparison, analysis.contextual)
    ):
        replacement = _formatted(analysis.contextual)
        assert replacement is not None
        contextual = ContextualSimplification(
            original=original,
            replacement=replacement,
        )

    return exact, contextual


def _finding_for_branch(
    branch: _engine.ConditionalBranch,
    ranges: dict[tuple[int, str], SourceRange],
) -> tuple[Finding, ...]:
    analysis = branch.analysis
    assert analysis is not None

    original = branch.expression_text
    exact, contextual = _simplifications_for_branch(branch)
    predicates = tuple(
        sorted(_engine.expression_predicates(branch.expression))
        if branch.expression is not None
        else ()
    )
    common = {
        "location": SourceLocation(branch.line),
        "directive": branch.directive,
        "original_condition": original,
        "exact_simplification": exact,
        "contextual_simplification": contextual,
        "opaque_predicates": predicates,
    }

    if analysis.status == "dead":
        return (
            Finding(
                kind=FindingKind.DEAD_BRANCH,
                reason=analysis.reason or "branch is unreachable",
                edit=None,
                **common,
            ),
        )

    if analysis.status == "redundant":
        return (
            Finding(
                kind=FindingKind.REDUNDANT_BRANCH,
                reason=analysis.reason or "condition is redundant in this context",
                edit=None,
                **common,
            ),
        )

    if branch.expression is None:
        return ()

    findings: list[Finding] = []
    if exact is not None:
        findings.append(
            Finding(
                kind=FindingKind.SIMPLIFIABLE_CONDITION,
                reason="condition has a globally equivalent simpler form",
                edit=_edit_for(
                    branch,
                    ranges,
                    exact.replacement,
                    FixConfidence.EXACT,
                ),
                **common,
            )
        )

    if contextual is not None:
        findings.append(
            Finding(
                kind=FindingKind.CONTEXTUAL_SIMPLIFICATION,
                reason="condition has a simpler equivalent under its branch context",
                edit=_edit_for(
                    branch,
                    ranges,
                    contextual.replacement,
                    FixConfidence.CONTEXTUAL,
                ),
                **common,
            )
        )

    return tuple(findings)


def _translate_parse_error(
    error: _engine.ConditionError,
    filename: str | None,
) -> ParseError:
    location = getattr(error, "location", None)
    public_location = None
    if location is not None and location.line is not None:
        public_location = SourceLocation(location.line, location.column)

    raw_code = getattr(error, "code", ErrorCode.EXPRESSION_SYNTAX.value)
    try:
        code = raw_code if isinstance(raw_code, ErrorCode) else ErrorCode(raw_code)
    except ValueError:
        code = ErrorCode.EXPRESSION_SYNTAX

    return ParseError(
        getattr(error, "message", str(error)),
        code=code,
        location=public_location,
        filename=filename,
    )


def analyze_source(source: str, *, filename: str | None = None) -> AnalysisResult:
    """Analyze C/C++ source text and return stable structured findings.

    The source is analyzed symbolically; no C/C++ preprocessing is performed.
    ``filename`` is optional metadata propagated to both successful results and
    structured :class:`CpreError` failures.

    Exact simplifications are globally equivalent to the source expression.
    Contextual simplifications are only equivalent within the effective branch
    context established by enclosing and preceding conditions. When a direct
    condition edit is available, its range uses one-based physical locations
    and an exclusive end position.
    """

    try:
        tree = _engine.analyze_source(source)
    except _engine.ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    ranges = _condition_ranges(source)
    findings = tuple(
        finding
        for branch in _branches(tree.groups)
        for finding in _finding_for_branch(branch, ranges)
    )
    return AnalysisResult(findings=findings, tree=tree, filename=filename)


ConditionalTree = _engine.ConditionalTree
ConditionError = CpreError

__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "ConditionError",
    "ConditionalTree",
    "ContextualSimplification",
    "CpreError",
    "ErrorCode",
    "ExactSimplification",
    "Finding",
    "FindingKind",
    "FixConfidence",
    "ParseError",
    "SourceLocation",
    "SourceRange",
    "SuggestedEdit",
    "analyze_source",
]
