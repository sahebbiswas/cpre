"""Option, result and diagnostic types shared by the public API and the core.

These types sit below the preprocessing core: ``cpre.api`` and ``cpre`` re-export
them, and modules such as ``preprocessing`` and ``configuration`` import them from
here, so the core never has to import the public facade.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from .errors import (
    AnalysisError,
    ErrorCode,
    ParseError,
    SourceLocation,
)
from .expressions import conjunction, negate
from .model import (
    TRUE,
    ConditionalTree,
    ConditionError,
    DefinedVariable,
    Expression,
    Variable,
)
from .robdd import AnalysisBudget as _AnalysisBudget
from .robdd import AnalysisLimitExceeded as _AnalysisLimitExceeded
from .robdd import ResourceLimits as _ResourceLimits


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
class AnalysisOptions:
    """Deterministic resource limits for one source analysis.

    ``max_atoms`` and ``max_bdd_nodes`` apply to each independent component of
    conditional groups (groups that share no Boolean atom are analyzed
    separately); ``max_work`` is a single cap shared by all components.
    """

    max_atoms: int = 64
    max_bdd_nodes: int = 100_000
    max_work: int = 500_000

    def __post_init__(self) -> None:
        for name in ("max_atoms", "max_bdd_nodes", "max_work"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise AnalysisError(
                    f"{name} must be a positive integer",
                    code=ErrorCode.ANALYSIS_FAILURE,
                )

    def _resource_limits(self) -> _ResourceLimits:
        return _ResourceLimits(
            max_atoms=self.max_atoms,
            max_bdd_nodes=self.max_bdd_nodes,
            max_work=self.max_work,
        )


@dataclass(frozen=True)
class AnalysisIncomplete:
    """Structured diagnostic describing intentionally curtailed exact analysis.

    ``component`` is ``None`` when the limit curtailed the whole analysis. For
    :func:`analyze_source` it is otherwise the index of the affected entry in
    :attr:`AnalysisResult.incomplete_components`; the rest of the source was
    still analyzed. ``source_identity`` names the resolved included source that
    ``location`` refers to during concrete preprocessing, or ``None`` for the
    primary source.
    """

    code: ErrorCode
    resource: str
    limit: int
    observed: int
    message: str
    location: SourceLocation | None = None
    component: int | None = None
    source_identity: str | None = None


@dataclass(frozen=True)
class IncompleteGroup:
    """A top-level conditional group (``#if`` ... ``#endif``) that was not analyzed."""

    location: SourceLocation
    end_line: int | None
    directive: str
    condition: str | None


@dataclass(frozen=True)
class IncompleteComponent:
    """Independent conditional groups whose exact analysis hit a resource limit.

    ``groups`` lists the top-level conditional groups of the component in source
    order; nested conditionals belong to their top-level group. ``atoms`` lists
    the component's Boolean atoms in first-occurrence order. ``diagnostics`` holds
    the component-local limit diagnostics, which also appear in
    :attr:`AnalysisResult.incomplete`. No finding is reported for any branch of
    these groups, and their branches in :attr:`AnalysisResult.tree` carry no
    analysis.
    """

    index: int
    groups: tuple[IncompleteGroup, ...]
    atoms: tuple[str, ...]
    diagnostics: tuple[AnalysisIncomplete, ...]

    @property
    def resource(self) -> str:
        """The first resource limit the component exceeded."""
        return self.diagnostics[0].resource

    def contains_line(self, line: int) -> bool:
        """Whether ``line`` lies inside one of the component's groups."""
        return any(
            group.location.line <= line <= (group.end_line or group.location.line)
            for group in self.groups
        )


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
    """Structured result returned by :func:`analyze_source`.

    ``complete`` is true only when every conditional was analyzed. An incomplete
    result is either *partial* (every diagnostic in ``incomplete`` names an entry
    of ``incomplete_components``; ``findings`` are proven for all other groups)
    or curtailed as a whole (``findings`` is empty).
    """

    findings: tuple[Finding, ...]
    tree: ConditionalTree
    filename: str | None = None
    incomplete: tuple[AnalysisIncomplete, ...] = ()
    incomplete_components: tuple[IncompleteComponent, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.incomplete

    @property
    def partial(self) -> bool:
        """Incomplete, but only in ``incomplete_components``; other groups were analyzed."""
        return bool(self.incomplete) and all(
            diagnostic.component is not None for diagnostic in self.incomplete
        )


def _translate_parse_error(error: ConditionError, filename: str | None) -> ParseError:
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


def _assumption_expression(assumptions: MacroAssumptions) -> Expression:
    terms: list[Expression] = []
    terms.extend(DefinedVariable(name) for name in sorted(assumptions.defined))
    terms.extend(negate(DefinedVariable(name)) for name in sorted(assumptions.undefined))
    for name, value in assumptions.values:
        variable = Variable(name)
        terms.append(variable if value else negate(variable))
    return conjunction(*terms) if terms else TRUE
