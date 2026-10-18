"""Stable programmatic API for cpre library consumers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING

from ._types import (
    AnalysisIncomplete,
    AnalysisOptions,
    AnalysisResult,
    AnalysisTelemetry,
    ComponentTelemetry,
    ContextualSimplification,
    ExactSimplification,
    Finding,
    FindingKind,
    FixConfidence,
    IncompleteComponent,
    IncompleteGroup,
    MacroAssumptions,
    SourceRange,
    SuggestedEdit,
    _assumption_expression,
    _normalize_assumptions,
    _translate_parse_error,
)
from .analysis import AnalysisTrace as _AnalysisTrace
from .analysis import PartialAnalysis as _PartialAnalysis
from .analysis import _clear_analysis as _engine_clear_analysis
from .analysis import analyze_tree_partial as _analyze_tree_partial
from .errors import (
    AnalysisError,
    CpreError,
    ErrorCode,
    IncompleteConfigurationError,
    ParseError,
    SourceLocation,
)
from .expressions import (
    expression_atoms_in_order,
    expression_predicates,
    expressions_differ,
    format_expression,
)
from .lexer import logical_lines
from .model import TRUE, ConditionalBranch, ConditionalGroup, ConditionalTree, Expression
from .model import ConditionError as ModelConditionError
from .parser import parse_source
from .robdd import BDD
from .robdd import AnalysisBudget as _AnalysisBudget
from .robdd import AnalysisLimitExceeded as _AnalysisLimitExceeded
from .robdd import ResourceLimits as _ResourceLimits


def _branches(groups: list[ConditionalGroup]) -> Iterator[ConditionalBranch]:
    for group in groups:
        for branch in group.branches:
            yield branch
            yield from _branches(branch.children)


def _formatted(expression: Expression | None) -> str | None:
    if expression is None:
        return None
    return format_expression(expression)


def _globally_equivalent(
    left: Expression,
    right: Expression,
    limits: _ResourceLimits,
    budget: _AnalysisBudget,
) -> bool:
    atoms = [atom for expression in (left, right) for atom in expression_atoms_in_order(expression)]
    bdd = BDD(atoms, limits=limits, budget=budget)
    return bdd.equivalent_under(TRUE, left, right)


def _condition_ranges(source: str) -> dict[tuple[int, str], SourceRange]:
    result: dict[tuple[int, str], SourceRange] = {}
    for logical_line in logical_lines(source):
        directive = logical_line.directive
        if directive is None or directive.name not in {"if", "elif"}:
            continue
        kind = directive.name
        remainder = logical_line.text[directive.name_end :]
        start_offset = len(remainder) - len(remainder.lstrip())
        end_offset = len(remainder.rstrip())
        if end_offset <= start_offset:
            continue
        locations = logical_line.locations[directive.name_end :]
        start = locations[start_offset]
        last = locations[end_offset - 1]
        if start.line is None or last.line is None:
            continue
        result[(logical_line.directive_line, kind)] = SourceRange(
            start=SourceLocation(start.line, start.column),
            end=SourceLocation(last.line, last.column + 1),
        )
    return result


def _edit_for(
    branch: ConditionalBranch,
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
    branch: ConditionalBranch,
    limits: _ResourceLimits,
    budget: _AnalysisBudget,
) -> tuple[ExactSimplification | None, ContextualSimplification | None]:
    analysis = branch.analysis
    assert analysis is not None
    if branch.expression is None or branch.expression_text is None:
        return None, None
    original = branch.expression_text
    exact: ExactSimplification | None = None
    if analysis.simplified is not None and expressions_differ(
        branch.expression, analysis.simplified
    ):
        replacement = _formatted(analysis.simplified)
        assert replacement is not None
        exact = ExactSimplification(original, replacement)
    comparison = analysis.simplified or branch.expression
    contextual: ContextualSimplification | None = None
    if (
        analysis.contextual is not None
        and expressions_differ(comparison, analysis.contextual)
        and not _globally_equivalent(comparison, analysis.contextual, limits, budget)
    ):
        replacement = _formatted(analysis.contextual)
        assert replacement is not None
        contextual = ContextualSimplification(original, replacement)
    return exact, contextual


def _analysis_dependency(
    branch: ConditionalBranch,
    baseline: ConditionalBranch | None,
    kind: FindingKind,
) -> bool:
    if baseline is None or baseline.analysis is None or branch.analysis is None:
        return False
    current = branch.analysis
    prior = baseline.analysis
    if kind in {FindingKind.DEAD_BRANCH, FindingKind.REDUNDANT_BRANCH}:
        return current.status != prior.status
    if kind is FindingKind.SIMPLIFIABLE_CONDITION:
        return expressions_differ(current.simplified, prior.simplified)
    return expressions_differ(current.contextual, prior.contextual)


def _finding_for_branch(
    branch: ConditionalBranch,
    ranges: dict[tuple[int, str], SourceRange],
    limits: _ResourceLimits,
    budget: _AnalysisBudget,
    baseline: ConditionalBranch | None = None,
) -> tuple[Finding, ...]:
    analysis = branch.analysis
    assert analysis is not None
    original = branch.expression_text
    try:
        exact, contextual = _simplifications_for_branch(branch, limits, budget)
    except _AnalysisLimitExceeded as error:
        if error.line is None:
            error.line = branch.line
        raise
    predicates = tuple(
        sorted(expression_predicates(branch.expression)) if branch.expression is not None else ()
    )

    def make_finding(kind: FindingKind, reason: str, edit: SuggestedEdit | None) -> Finding:
        return Finding(
            kind=kind,
            location=SourceLocation(branch.line),
            directive=branch.directive,
            original_condition=original,
            exact_simplification=exact,
            contextual_simplification=contextual,
            reason=reason,
            opaque_predicates=predicates,
            edit=edit,
            depends_on_assumptions=_analysis_dependency(branch, baseline, kind),
        )

    if analysis.status == "dead":
        return (
            make_finding(
                FindingKind.DEAD_BRANCH,
                analysis.reason or "branch is unreachable",
                None,
            ),
        )
    if analysis.status == "redundant":
        return (
            make_finding(
                FindingKind.REDUNDANT_BRANCH,
                analysis.reason or "condition is redundant in this context",
                None,
            ),
        )
    if branch.expression is None:
        return ()
    findings: list[Finding] = []
    if exact is not None:
        findings.append(
            make_finding(
                FindingKind.SIMPLIFIABLE_CONDITION,
                "condition has an exact simpler form",
                _edit_for(branch, ranges, exact.replacement, FixConfidence.EXACT),
            )
        )
    if contextual is not None:
        findings.append(
            make_finding(
                FindingKind.CONTEXTUAL_SIMPLIFICATION,
                "condition has a simpler equivalent under its branch context",
                _edit_for(branch, ranges, contextual.replacement, FixConfidence.CONTEXTUAL),
            )
        )
    return tuple(findings)


def _incomplete_diagnostic(
    error: _AnalysisLimitExceeded, component: int | None = None
) -> AnalysisIncomplete:
    return AnalysisIncomplete(
        code=ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
        resource=error.resource,
        limit=error.limit,
        observed=error.observed,
        message=str(error),
        location=SourceLocation(error.line) if error.line is not None else None,
        component=component,
    )


def _telemetry(
    trace: _AnalysisTrace,
    options: AnalysisOptions,
    budget: _AnalysisBudget,
    *,
    failures: dict[int, _AnalysisLimitExceeded],
    positions: dict[int, int],
    curtailed: bool,
) -> AnalysisTelemetry:
    """The opt-in telemetry for one analysis; ``positions`` maps component ids to
    :class:`IncompleteComponent` indices."""
    components = []
    for item in trace.components:
        nodes = len(item.bdd.nodes) - 2 if item.bdd is not None else 0
        if item.id in failures:
            status, resource = "incomplete", failures[item.id].resource
        elif curtailed:
            status, resource = "unfinished", None
        else:
            status, resource = "analyzed", None
        components.append(
            ComponentTelemetry(
                atoms=item.atoms,
                groups=item.groups,
                nodes=nodes,
                status=status,
                resource=resource,
                incomplete_component=positions.get(item.id),
            )
        )
    return AnalysisTelemetry(
        atoms=trace.atoms,
        components=tuple(components),
        limits=options,
        work=budget.work,
    )


def _incomplete_result(
    source: str,
    *,
    filename: str | None,
    distinguish_defined: bool,
    error: _AnalysisLimitExceeded,
    telemetry: AnalysisTelemetry | None = None,
) -> AnalysisResult:
    tree = parse_source(source, distinguish_defined=distinguish_defined)
    return AnalysisResult(
        findings=(),
        tree=tree,
        filename=filename,
        incomplete=(_incomplete_diagnostic(error),),
        telemetry=telemetry,
    )


def _incomplete_group(group: ConditionalGroup) -> IncompleteGroup:
    opening = group.branches[0]
    return IncompleteGroup(
        location=SourceLocation(group.line),
        end_line=group.end_line,
        directive=opening.directive,
        condition=opening.expression_text,
    )


def _group_findings(
    group: ConditionalGroup,
    baseline: ConditionalGroup | None,
    ranges: dict[tuple[int, str], SourceRange],
    limits: _ResourceLimits,
    budget: _AnalysisBudget,
) -> tuple[Finding, ...]:
    branches = tuple(_branches([group]))
    baseline_branches = tuple(_branches([baseline])) if baseline is not None else ()
    return tuple(
        finding
        for index, branch in enumerate(branches)
        for finding in _finding_for_branch(
            branch,
            ranges,
            limits,
            budget,
            baseline_branches[index] if index < len(baseline_branches) else None,
        )
    )


def analyze_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    options: AnalysisOptions | None = None,
    telemetry: bool = False,
) -> AnalysisResult:
    """Analyze source with optional assumptions and deterministic resource limits.

    ``telemetry=True`` additionally fills :attr:`AnalysisResult.telemetry` with
    diagnostic resource figures (see :class:`AnalysisTelemetry`); findings and
    incomplete results are identical with it on or off.

    Limits are applied per independent component as described on
    :class:`AnalysisOptions`. When a component exceeds ``max_atoms`` or
    ``max_bdd_nodes``, the result is incomplete and *partial*: that component is
    described in :attr:`AnalysisResult.incomplete_components` and contributes no
    findings, while findings for every other component are still reported. When
    the shared ``max_work`` budget is exhausted, the whole result is incomplete
    and contains no findings. A partial proof is never reported as a finding.
    """

    normalized = _normalize_assumptions(assumptions)
    resolved_options = options or AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance",
            code=ErrorCode.ANALYSIS_FAILURE,
        )
    limits = resolved_options._resource_limits()
    budget = _AnalysisBudget(limits.max_work)
    assumption_expression = _assumption_expression(normalized) if normalized is not None else None
    trace = _AnalysisTrace()
    try:
        main = _analyze_tree_partial(
            parse_source(source, distinguish_defined=normalized is not None),
            assumptions=assumption_expression,
            limits=limits,
            budget=budget,
            trace=trace,
        )
        baseline: _PartialAnalysis | None = (
            _analyze_tree_partial(parse_source(source), limits=limits, budget=budget)
            if normalized is not None
            else None
        )
        tree = main.tree
        failures = dict(main.failures)
        if baseline is not None:
            # A finding's ``depends_on_assumptions`` needs the baseline proof too.
            for index, component in enumerate(baseline.group_components):
                if component in baseline.failures and index < len(main.group_components):
                    failures.setdefault(main.group_components[index], baseline.failures[component])
        ranges = _condition_ranges(source)
        group_findings: list[tuple[Finding, ...]] = []
        for index, group in enumerate(tree.groups):
            component = main.group_components[index]
            if component in failures:
                group_findings.append(())
                continue
            baseline_group = (
                baseline.tree.groups[index]
                if baseline is not None and index < len(baseline.tree.groups)
                else None
            )
            try:
                group_findings.append(
                    _group_findings(group, baseline_group, ranges, limits, budget)
                )
            except _AnalysisLimitExceeded as error:
                if error.resource == "work":
                    raise
                failures[component] = error
                group_findings.append(())
    except _AnalysisLimitExceeded as error:
        return _incomplete_result(
            source,
            filename=filename,
            distinguish_defined=normalized is not None,
            error=error,
            telemetry=(
                _telemetry(
                    trace, resolved_options, budget, failures={}, positions={}, curtailed=True
                )
                if telemetry
                else None
            ),
        )
    except ModelConditionError as error:
        raise _translate_parse_error(error, filename) from error

    findings = tuple(
        finding
        for index, found in enumerate(group_findings)
        if main.group_components[index] not in failures
        for finding in found
    )
    failed_groups: dict[int, list[ConditionalGroup]] = {}
    for index, group in enumerate(tree.groups):
        component = main.group_components[index]
        if component in failures:
            failed_groups.setdefault(component, []).append(group)
            _engine_clear_analysis([group])
    components: list[IncompleteComponent] = []
    for position, (component, groups) in enumerate(failed_groups.items()):
        diagnostic = _incomplete_diagnostic(failures[component], position)
        components.append(
            IncompleteComponent(
                index=position,
                groups=tuple(_incomplete_group(group) for group in groups),
                atoms=tuple(
                    format_expression(atom) for atom in main.component_atoms.get(component, ())
                ),
                diagnostics=(diagnostic,),
            )
        )
    return AnalysisResult(
        findings=findings,
        tree=tree,
        filename=filename,
        incomplete=tuple(diagnostic for item in components for diagnostic in item.diagnostics),
        incomplete_components=tuple(components),
        telemetry=(
            _telemetry(
                trace,
                resolved_options,
                budget,
                failures=failures,
                positions={component: position for position, component in enumerate(failed_groups)},
                curtailed=False,
            )
            if telemetry
            else None
        ),
    )


ConditionError = CpreError

# The public types are defined in ``cpre._types`` (below the core) but belong to this module as
# far as users, ``repr``, pickles and documentation tools are concerned.
for _public in (
    AnalysisIncomplete,
    AnalysisOptions,
    AnalysisResult,
    AnalysisTelemetry,
    ComponentTelemetry,
    ContextualSimplification,
    ExactSimplification,
    Finding,
    FindingKind,
    FixConfidence,
    IncompleteComponent,
    IncompleteGroup,
    MacroAssumptions,
    SourceRange,
    SuggestedEdit,
):
    _public.__module__ = __name__
del _public

__all__ = [
    "AnalysisError",
    "AnalysisIncomplete",
    "AnalysisOptions",
    "AnalysisResult",
    "AnalysisTelemetry",
    "ComponentTelemetry",
    "ConditionError",
    "ConditionalTree",
    "ContextualSimplification",
    "CpreError",
    "ErrorCode",
    "ExactSimplification",
    "Finding",
    "FindingKind",
    "FixConfidence",
    "IncompleteComponent",
    "IncompleteConfigurationError",
    "IncompleteGroup",
    "MacroAssumptions",
    "ParseError",
    "SourceLocation",
    "SourceRange",
    "SuggestedEdit",
    "analyze_source",
]
