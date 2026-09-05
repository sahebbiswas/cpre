"""Stable programmatic API for cpre library consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Mapping

from . import cpre as _engine
from .errors import AnalysisError, CpreError, ErrorCode, ParseError, SourceLocation
from .expressions import conjunction, negate
from .model import DefinedVariable, TRUE, Variable


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


@dataclass(frozen=True, init=False)
class MacroAssumptions:
    """Explicit macro state supplied by a library caller.

    ``defined`` and ``undefined`` constrain macro definedness. ``values``
    constrains the Boolean truth of bare macro identifiers independently, so a
    macro may be known-defined while also having the Boolean value ``False``.
    Unmentioned macros remain symbolic.
    """

    defined: frozenset[str]
    undefined: frozenset[str]
    values: tuple[tuple[str, bool], ...]

    def __init__(
        self,
        *,
        defined: Iterable[str] = (),
        undefined: Iterable[str] = (),
        values: Mapping[str, bool] | None = None,
    ) -> None:
        normalized_defined = frozenset(defined)
        normalized_undefined = frozenset(undefined)
        normalized_values = dict(values or {})
        all_names = normalized_defined | normalized_undefined | set(normalized_values)
        invalid = sorted(name for name in all_names if not re.fullmatch(r"[A-Za-z_]\w*", name))
        if invalid:
            raise AnalysisError(
                f"invalid macro name in assumptions: {invalid[0]!r}",
                code=ErrorCode.INVALID_ASSUMPTIONS,
            )
        overlap = sorted(normalized_defined & normalized_undefined)
        if overlap:
            raise AnalysisError(
                f"macro cannot be both defined and undefined: {overlap[0]}",
                code=ErrorCode.INVALID_ASSUMPTIONS,
            )
        for name, value in normalized_values.items():
            if type(value) is not bool:
                raise AnalysisError(
                    f"Boolean macro assumption for {name} must be True or False",
                    code=ErrorCode.INVALID_ASSUMPTIONS,
                )
            if name in normalized_undefined and value:
                raise AnalysisError(
                    f"undefined macro cannot have a true Boolean value: {name}",
                    code=ErrorCode.INVALID_ASSUMPTIONS,
                )
        object.__setattr__(self, "defined", normalized_defined)
        object.__setattr__(self, "undefined", normalized_undefined)
        object.__setattr__(self, "values", tuple(sorted(normalized_values.items())))

    @property
    def is_empty(self) -> bool:
        return not (self.defined or self.undefined or self.values)


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
    """An exact replacement for a source condition.

    Without macro assumptions, ``replacement`` is Boolean-equivalent to
    ``original`` for every assignment of the modeled predicates. With explicit
    assumptions, equivalence is guaranteed for every assignment consistent with
    those assumptions.
    """

    original: str
    replacement: str


@dataclass(frozen=True)
class ContextualSimplification:
    """A replacement equivalent only in the branch's reachable context."""

    original: str
    replacement: str


@dataclass(frozen=True)
class Finding:
    """A structured preprocessor-analysis finding.

    ``depends_on_assumptions`` is true only when supplied macro assumptions
    materially changed the status or simplification proof that produced this
    finding. This lets downstream analyzers distinguish universal diagnostics
    from configuration-specific ones.
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
    depends_on_assumptions: bool = False

    @property
    def simplified_condition(self) -> str | None:
        if self.exact_simplification is None:
            return None
        return self.exact_simplification.replacement

    @property
    def contextual_condition(self) -> str | None:
        if self.contextual_simplification is None:
            return None
        return self.contextual_simplification.replacement


@dataclass(frozen=True)
class AnalysisResult:
    """Structured result returned by :func:`analyze_source`."""

    findings: tuple[Finding, ...]
    tree: _engine.ConditionalTree
    filename: str | None = None


def _branches(groups: list[_engine.ConditionalGroup]) -> Iterator[_engine.ConditionalBranch]:
    for group in groups:
        for branch in group.branches:
            yield branch
            yield from _branches(branch.children)


def _formatted(expression: _engine.Expression | None) -> str | None:
    if expression is None:
        return None
    return _engine.format_expression(expression)


def _globally_equivalent(left: _engine.Expression, right: _engine.Expression) -> bool:
    atoms = [
        atom
        for expression in (left, right)
        for atom in _engine._expression_atoms_in_order(expression)
    ]
    bdd = _engine._BDD(atoms)
    return bdd.equivalent_under(_engine.TRUE, left, right)


def _condition_ranges(source: str) -> dict[tuple[int, str], SourceRange]:
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
    return SuggestedEdit(source_range, replacement, confidence)


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
        exact = ExactSimplification(original, replacement)
    comparison = analysis.simplified or branch.expression
    contextual: ContextualSimplification | None = None
    if (
        analysis.contextual is not None
        and _engine._expressions_differ(comparison, analysis.contextual)
        and not _globally_equivalent(comparison, analysis.contextual)
    ):
        replacement = _formatted(analysis.contextual)
        assert replacement is not None
        contextual = ContextualSimplification(original, replacement)
    return exact, contextual


def _analysis_dependency(
    branch: _engine.ConditionalBranch,
    baseline: _engine.ConditionalBranch | None,
    kind: FindingKind,
) -> bool:
    if baseline is None or baseline.analysis is None or branch.analysis is None:
        return False
    current = branch.analysis
    prior = baseline.analysis
    if kind in {FindingKind.DEAD_BRANCH, FindingKind.REDUNDANT_BRANCH}:
        return current.status != prior.status
    if kind is FindingKind.SIMPLIFIABLE_CONDITION:
        return _engine._expressions_differ(current.simplified, prior.simplified)
    return _engine._expressions_differ(current.contextual, prior.contextual)


def _finding_for_branch(
    branch: _engine.ConditionalBranch,
    ranges: dict[tuple[int, str], SourceRange],
    baseline: _engine.ConditionalBranch | None = None,
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
        kind = FindingKind.DEAD_BRANCH
        return (Finding(kind=kind, reason=analysis.reason or "branch is unreachable", edit=None,
                        depends_on_assumptions=_analysis_dependency(branch, baseline, kind), **common),)
    if analysis.status == "redundant":
        kind = FindingKind.REDUNDANT_BRANCH
        return (Finding(kind=kind, reason=analysis.reason or "condition is redundant in this context", edit=None,
                        depends_on_assumptions=_analysis_dependency(branch, baseline, kind), **common),)
    if branch.expression is None:
        return ()
    findings: list[Finding] = []
    if exact is not None:
        kind = FindingKind.SIMPLIFIABLE_CONDITION
        findings.append(Finding(
            kind=kind,
            reason="condition has an exact simpler form",
            edit=_edit_for(branch, ranges, exact.replacement, FixConfidence.EXACT),
            depends_on_assumptions=_analysis_dependency(branch, baseline, kind),
            **common,
        ))
    if contextual is not None:
        kind = FindingKind.CONTEXTUAL_SIMPLIFICATION
        findings.append(Finding(
            kind=kind,
            reason="condition has a simpler equivalent under its branch context",
            edit=_edit_for(branch, ranges, contextual.replacement, FixConfidence.CONTEXTUAL),
            depends_on_assumptions=_analysis_dependency(branch, baseline, kind),
            **common,
        ))
    return tuple(findings)


def _translate_parse_error(error: _engine.ConditionError, filename: str | None) -> ParseError:
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


def _normalize_assumptions(
    assumptions: MacroAssumptions | Mapping[str, bool] | None,
) -> MacroAssumptions | None:
    if assumptions is None:
        return None
    if isinstance(assumptions, MacroAssumptions):
        return None if assumptions.is_empty else assumptions
    if isinstance(assumptions, Mapping):
        normalized = MacroAssumptions(values=assumptions)
        return None if normalized.is_empty else normalized
    raise AnalysisError(
        "assumptions must be a MacroAssumptions instance or mapping of macro names to bool",
        code=ErrorCode.INVALID_ASSUMPTIONS,
    )


def _assumption_expression(assumptions: MacroAssumptions) -> _engine.Expression:
    terms: list[_engine.Expression] = []
    terms.extend(DefinedVariable(name) for name in sorted(assumptions.defined))
    terms.extend(negate(DefinedVariable(name)) for name in sorted(assumptions.undefined))
    for name, value in assumptions.values:
        variable = Variable(name)
        terms.append(variable if value else negate(variable))
    return conjunction(*terms) if terms else TRUE


def analyze_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
) -> AnalysisResult:
    """Analyze source with optional explicit macro assumptions.

    A plain mapping constrains Boolean values of bare macro identifiers. Use
    :class:`MacroAssumptions` when defined/undefined state must be expressed
    independently. Unknown macros always remain symbolic.
    """

    normalized = _normalize_assumptions(assumptions)
    try:
        tree = _engine.analyze_source(
            source,
            assumptions=_assumption_expression(normalized) if normalized is not None else None,
        )
        baseline_tree = _engine.analyze_source(source) if normalized is not None else None
    except _engine.ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    ranges = _condition_ranges(source)
    branches = tuple(_branches(tree.groups))
    baseline_branches = tuple(_branches(baseline_tree.groups)) if baseline_tree is not None else ()
    findings = tuple(
        finding
        for index, branch in enumerate(branches)
        for finding in _finding_for_branch(
            branch,
            ranges,
            baseline_branches[index] if index < len(baseline_branches) else None,
        )
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
    "MacroAssumptions",
    "ParseError",
    "SourceLocation",
    "SourceRange",
    "SuggestedEdit",
    "analyze_source",
]
