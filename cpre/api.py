"""Stable programmatic API for cpre library consumers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from . import cpre as _engine


class FindingKind(str, Enum):
    """Machine-readable categories emitted by :func:`analyze_source`."""

    DEAD_BRANCH = "dead_branch"
    REDUNDANT_BRANCH = "redundant_branch"
    SIMPLIFIABLE_CONDITION = "simplifiable_condition"
    CONTEXTUAL_SIMPLIFICATION = "contextual_simplification"


@dataclass(frozen=True)
class SourceLocation:
    """One-based physical source location for a finding."""

    line: int
    column: int | None = None


@dataclass(frozen=True)
class Finding:
    """A structured preprocessor-analysis finding."""

    kind: FindingKind
    location: SourceLocation
    directive: str
    original_condition: str | None
    simplified_condition: str | None
    contextual_condition: str | None
    reason: str
    opaque_predicates: tuple[str, ...] = ()


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


def _finding_for_branch(
    branch: _engine.ConditionalBranch,
) -> tuple[Finding, ...]:
    analysis = branch.analysis
    assert analysis is not None

    original = branch.expression_text
    simplified = _formatted(analysis.simplified)
    contextual = _formatted(analysis.contextual)
    predicates = tuple(
        sorted(_engine.expression_predicates(branch.expression))
        if branch.expression is not None
        else ()
    )
    common = {
        "location": SourceLocation(branch.line),
        "directive": branch.directive,
        "original_condition": original,
        "simplified_condition": simplified,
        "contextual_condition": contextual,
        "opaque_predicates": predicates,
    }

    if analysis.status == "dead":
        return (
            Finding(
                kind=FindingKind.DEAD_BRANCH,
                reason=analysis.reason or "branch is unreachable",
                **common,
            ),
        )

    if analysis.status == "redundant":
        return (
            Finding(
                kind=FindingKind.REDUNDANT_BRANCH,
                reason=analysis.reason or "condition is redundant in this context",
                **common,
            ),
        )

    if branch.expression is None:
        return ()

    findings: list[Finding] = []
    if analysis.simplified is not None and _engine._expressions_differ(
        branch.expression, analysis.simplified
    ):
        findings.append(
            Finding(
                kind=FindingKind.SIMPLIFIABLE_CONDITION,
                reason="condition has a globally equivalent simpler form",
                **common,
            )
        )

    comparison = analysis.simplified or branch.expression
    if analysis.contextual is not None and _engine._expressions_differ(
        comparison, analysis.contextual
    ):
        findings.append(
            Finding(
                kind=FindingKind.CONTEXTUAL_SIMPLIFICATION,
                reason="condition has a simpler equivalent under its branch context",
                **common,
            )
        )

    return tuple(findings)


def analyze_source(source: str, *, filename: str | None = None) -> AnalysisResult:
    """Analyze C/C++ source text and return stable structured findings.

    The source is analyzed symbolically; no C/C++ preprocessing is performed.
    ``filename`` is optional metadata for downstream callers and does not affect
    analysis semantics. Malformed conditional input raises :class:`ConditionError`.
    """

    tree = _engine.analyze_source(source)
    findings = tuple(
        finding
        for branch in _branches(tree.groups)
        for finding in _finding_for_branch(branch)
    )
    return AnalysisResult(findings=findings, tree=tree, filename=filename)


ConditionalTree = _engine.ConditionalTree
ConditionError = _engine.ConditionError

__all__ = [
    "AnalysisResult",
    "ConditionError",
    "ConditionalTree",
    "Finding",
    "FindingKind",
    "SourceLocation",
    "analyze_source",
]
