"""Branch-covering concrete macro configurations.

Targets come from exact Boolean reasoning over the conditional structure; every
generated configuration is then verified with concrete preprocessing, so an
outcome is reported as covered only when preprocessing actually selected it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .analysis import _macro_semantics, tree_expressions
from .api import AnalysisIncomplete, AnalysisOptions, _translate_parse_error
from .configuration import MacroConfiguration, UnknownNamePolicy
from .errors import AnalysisError, ErrorCode
from .expressions import conjunction, expression_atoms_in_order, negate
from .include_queries import IncludeQueryProvider
from .includes import DEFAULT_MAX_INCLUDE_DEPTH, IncludeResolver
from .model import (
    TRUE,
    BooleanAtom,
    ConditionalGroup,
    ConditionalTree,
    ConditionError,
    DefinedVariable,
    Expression,
    Variable,
)
from .parser import logical_lines, parse_source
from .pragmas import PragmaHandler, preprocess_source
from .preprocessing import _SELECTED_BRANCHES, PreprocessingContext
from .proofs import WitnessAssignment, _atom_key, _public_assignment
from .robdd import BDD, AnalysisBudget, AnalysisLimitExceeded

_ASSIGNMENT_RE = re.compile(r"\s*#\s*(?:define|undef)\s+([A-Za-z_]\w*)")


class BranchOutcomeStatus(str, Enum):
    """Coverage status of one conditional-group outcome."""

    COVERED = "covered"
    UNSATISFIABLE = "unsatisfiable"
    NOT_COVERED = "not_covered"


@dataclass(frozen=True)
class BranchOutcome:
    """One outcome of a conditional group in the primary source.

    ``branch_line`` and ``directive`` identify the selected branch. Both are
    ``None`` for the outcome in which no branch of a group without ``#else`` is
    selected. ``configuration`` is the index of the first configuration in
    :attr:`BranchCoverageResult.configurations` that selects this outcome.
    """

    group_line: int
    branch_line: int | None
    directive: str | None
    status: BranchOutcomeStatus
    configuration: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CoverageConfiguration:
    """One verified concrete configuration and the outcomes it selects.

    ``configuration`` is closed-world and names every conditional macro: each
    is defined or explicitly undefined. ``required`` is a reduced set of Boolean
    assignments that by itself guarantees, in the Boolean model, the outcomes this
    configuration was generated for; ``dont_care`` names conditional macros that
    set leaves free, which ``configuration`` still pins to one concrete choice.
    ``covers`` holds indices into :attr:`BranchCoverageResult.outcomes`.
    """

    configuration: MacroConfiguration
    required: tuple[WitnessAssignment, ...]
    dont_care: tuple[str, ...]
    covers: tuple[int, ...]


@dataclass(frozen=True)
class BranchCoverageResult:
    """Result of :func:`cover_branches`; atomic when exact analysis is incomplete."""

    configurations: tuple[CoverageConfiguration, ...] | None
    outcomes: tuple[BranchOutcome, ...] | None
    incomplete: AnalysisIncomplete | None = None

    @property
    def complete(self) -> bool:
        return self.incomplete is None


class _VerificationLimit(Exception):
    def __init__(self, incomplete: AnalysisIncomplete) -> None:
        super().__init__(incomplete.message)
        self.incomplete = incomplete


@dataclass(frozen=True)
class _Target:
    group_line: int
    branch_line: int | None
    directive: str | None
    expression: Expression

    @property
    def key(self) -> tuple[int, int | None]:
        return (self.group_line, self.branch_line)


def _targets(groups: list[ConditionalGroup], context: Expression, found: list[_Target]) -> None:
    """Collect every group outcome with its path condition, in source order."""
    for group in groups:
        earlier: list[Expression] = []
        has_else = False
        for branch in group.branches:
            condition = branch.expression if branch.expression is not None else TRUE
            has_else = has_else or branch.directive == "else"
            path = conjunction(context, *(negate(item) for item in earlier), condition)
            found.append(_Target(group.line, branch.line, branch.directive, path))
            _targets(branch.children, path, found)
            earlier.append(condition)
        if not has_else:
            path = conjunction(context, *(negate(item) for item in earlier))
            found.append(_Target(group.line, None, None, path))


def _cube(bdd: BDD, root: int) -> dict[BooleanAtom, bool]:
    """Atoms fixed on the false-first satisfying path; absent atoms are don't-cares."""
    values: dict[BooleanAtom, bool] = {}
    node = root
    while node >= 2:
        bdd.budget.consume()
        item = bdd.nodes[node]
        assert item is not None
        variable, low, high = item
        values[bdd.atoms[variable]] = low == 0
        node = high if low == 0 else low
    return values


def _required(
    bdd: BDD, base: int, goal: int, cube: dict[BooleanAtom, bool]
) -> dict[BooleanAtom, bool]:
    """Drop witness literals that ``goal`` does not need under the macro semantics.

    A literal is kept only if, without it, some assignment consistent with the
    remaining literals and ``base`` would violate ``goal``.
    """
    required = dict(cube)
    missed = bdd.negate(goal)
    for atom in sorted(cube, key=_atom_key, reverse=True):
        trial = {key: value for key, value in required.items() if key != atom}
        node = base
        for key, value in trial.items():
            literal = bdd.build(key)
            node = bdd.apply("and", node, literal if value else bdd.negate(literal))
        if bdd.apply("and", node, missed) == 0:
            required = trial
    return required


def _configuration(
    cube: dict[BooleanAtom, bool], required: dict[BooleanAtom, bool], names: list[str]
) -> tuple[MacroConfiguration, tuple[str, ...]]:
    integers: dict[str, int] = {}
    undefined: set[str] = set()
    dont_care: list[str] = []
    for name in names:
        defined = cube.get(DefinedVariable(name))
        value = cube.get(Variable(name))
        if value is True or (defined is True and value is None):
            integers[name] = 1
        elif defined is True:
            integers[name] = 0
        else:
            # Explicit, so skipped includes cannot reopen the name (closed-world
            # defaulting stops at a skipped include).
            undefined.add(name)
        if DefinedVariable(name) not in required and Variable(name) not in required:
            dont_care.append(name)
    configuration = MacroConfiguration(
        integers=integers, undefined=undefined, unknown_names=UnknownNamePolicy.UNDEFINED
    )
    return configuration, tuple(dont_care)


def cover_branches(
    source: str,
    *,
    filename: str | None = None,
    context: PreprocessingContext | None = None,
    options: AnalysisOptions | None = None,
    include_query: IncludeQueryProvider | None = None,
    pragma_handler: PragmaHandler | None = None,
    skip_includes: bool = False,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
) -> BranchCoverageResult:
    """Generate concrete macro configurations that cover conditional-branch outcomes.

    An outcome is one branch of a primary-source conditional group being
    selected, or no branch being selected for a group without ``#else``. Each
    outcome's path condition is built from the enclosing branches; outcomes whose
    path condition is unsatisfiable are reported as ``UNSATISFIABLE``.

    Top-level groups that share no macro are independent components, analyzed
    with separate Boolean managers so resource limits apply per component, as in
    :func:`cpre.analyze_source`. Within a component, plans are generated greedily
    in source order: the first unplanned outcome is conjoined with every later
    unplanned outcome that stays jointly satisfiable. The k-th plans of all
    components are combined, and a deterministic false-first witness becomes a
    closed-world :class:`MacroConfiguration`. Each configuration is then
    run through :func:`cpre.preprocess_source` (with ``context``, ``include_query``,
    ``pragma_handler``, and the include options) and credited only with the
    outcomes preprocessing actually selected. Outcomes still uncovered get one
    more configuration that targets each of them alone. Configurations whose outcomes are
    all covered by others are dropped. The set is therefore sufficient for every
    ``COVERED`` outcome, but greedy rather than minimal.

    A satisfiable outcome the generated configurations do not reach is
    ``NOT_COVERED`` with a reason. This happens when the Boolean model and
    concrete preprocessing disagree: source-order ``#define``/``#undef``, included
    headers, names fixed by ``context``, or opaque comparisons such as
    ``VERSION >= 3``, for which cpre never invents integer values.

    Macros are assigned ``1`` (defined and true), ``0`` (defined and false), or
    left undefined. When exact analysis, or concrete preprocessing of a generated
    configuration, exceeds a resource limit, the result is atomic:
    ``configurations`` and ``outcomes`` are ``None`` and ``incomplete`` explains
    the limit. Malformed conditionals raise :class:`ParseError`.
    """
    resolved_options = options if options is not None else AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance", code=ErrorCode.ANALYSIS_FAILURE
        )
    try:
        tree = parse_source(source, distinguish_defined=True)
    except ConditionError as error:
        raise _translate_parse_error(error, filename) from error

    targets: list[_Target] = []
    owners: list[int] = []
    for number, group in enumerate(tree.groups):
        before = len(targets)
        _targets([group], TRUE, targets)
        owners.extend([number] * (len(targets) - before))
    index = {target.key: position for position, target in enumerate(targets)}
    fixed = set(context.standard_macros) if context is not None else set()
    assigned = {
        match[1]
        for line in logical_lines(source)
        if (match := _ASSIGNMENT_RE.match(line.text)) is not None
    }

    # Top-level groups that share no macro or predicate are independent, so each
    # component gets its own small BDD; per-component plans are zipped together.
    group_atoms = [
        list(
            dict.fromkeys(
                atom
                for expression in tree_expressions([group])
                for atom in expression_atoms_in_order(expression)
            )
        )
        for group in tree.groups
    ]
    parent = list(range(len(tree.groups)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    owner_of_key: dict[str, int] = {}
    for number, found in enumerate(group_atoms):
        for atom in found:
            key = atom.name if isinstance(atom, Variable) else f"\0{atom.text}"
            other = owner_of_key.setdefault(key, number)
            parent[find(number)] = find(other)
    members: dict[int, list[int]] = {}
    for number in range(len(tree.groups)):
        members.setdefault(find(number), []).append(number)
    components = sorted(members.values())
    component_of = {number: rank for rank, groups in enumerate(components) for number in groups}

    names = sorted(
        {atom.name for found in group_atoms for atom in found if isinstance(atom, Variable)} - fixed
    )
    limits = resolved_options._resource_limits()
    budget = AnalysisBudget(limits.max_work)
    status: dict[int, BranchOutcomeStatus] = {}
    reasons: dict[int, str] = {}

    def run(configuration: MacroConfiguration) -> tuple[set[int], str | None]:
        selections: dict[int, int | None] = {}
        token = _SELECTED_BRANCHES.set(selections)
        try:
            result = preprocess_source(
                source,
                filename=filename,
                configuration=configuration,
                context=context,
                options=resolved_options,
                include_query=include_query,
                pragma_handler=pragma_handler,
                skip_includes=skip_includes,
                include_resolver=include_resolver,
                max_include_depth=max_include_depth,
            )
        finally:
            _SELECTED_BRANCHES.reset(token)
        if not result.complete:
            problem = result.incomplete[0]
            if isinstance(problem, AnalysisIncomplete):
                # Verification ran out of resources; no configuration can do better.
                raise _VerificationLimit(problem)
            where = f"line {problem.location.line}: " if problem.location is not None else ""
            return set(), f"preprocessing was incomplete: {where}{problem.message}"
        return {index[key] for key in selections.items() if key in index}, None

    generated: list[tuple[CoverageConfiguration, set[int]]] = []

    def attempt(
        choices: list[tuple[BDD, int, int]],
    ) -> tuple[set[int], str | None]:
        """Verify the combination of one (bdd, base, root) choice per component."""
        cube: dict[BooleanAtom, bool] = {}
        needed: dict[BooleanAtom, bool] = {}
        for bdd, base, root in choices:
            part = _cube(bdd, root)
            cube.update(part)
            needed.update(_required(bdd, base, root, part))
        configuration, dont_care = _configuration(cube, needed, names)
        selected, failure = run(configuration)
        if selected:
            required = tuple(
                _public_assignment(atom, needed[atom]) for atom in sorted(needed, key=_atom_key)
            )
            generated.append(
                (CoverageConfiguration(configuration, required, dont_care, ()), selected)
            )
            for position in selected:
                # Concrete selection proves reachability.
                status[position] = BranchOutcomeStatus.COVERED
                reasons.pop(position, None)
        return selected, failure

    try:
        managers: list[tuple[BDD, int]] = []
        plans: list[list[int]] = []
        roots: dict[int, int] = {}
        for rank, groups in enumerate(components):
            local = [atom for number in groups for atom in group_atoms[number]]
            semantics = _macro_semantics(
                ConditionalTree([tree.groups[number] for number in groups]),
                legacy_symbolic=False,
            )
            local = list(dict.fromkeys([*local, *expression_atoms_in_order(semantics)]))
            bdd = BDD(local, limits=limits, budget=budget)
            base = bdd.build(semantics)
            managers.append((bdd, base))
            mine = [p for p in range(len(targets)) if component_of[owners[p]] == rank]
            for position in mine:
                roots[position] = bdd.apply("and", base, bdd.build(targets[position].expression))
                if roots[position] == 0:
                    status[position] = BranchOutcomeStatus.UNSATISFIABLE
                    reasons[position] = "the outcome's path condition is unsatisfiable"
            planned: set[int] = set()
            component_plans: list[int] = []
            for position in mine:
                if position in status or position in planned:
                    continue
                merged = roots[position]
                planned.add(position)
                for later in mine:
                    if later in status or later in planned:
                        continue
                    candidate = bdd.apply("and", merged, roots[later])
                    if candidate != 0:
                        merged = candidate
                        planned.add(later)
                component_plans.append(merged)
            plans.append(component_plans or [base])

        def default(rank: int) -> tuple[BDD, int, int]:
            bdd, base = managers[rank]
            return bdd, base, plans[rank][0]

        for step in range(max(len(plan) for plan in plans) if plans else 0):
            attempt(
                [
                    (*managers[rank], plans[rank][step])
                    if step < len(plans[rank])
                    else default(rank)
                    for rank in range(len(components))
                ]
            )

        for position in range(len(targets)):
            if position in status:
                continue
            rank = component_of[owners[position]]
            choices = [default(other) for other in range(len(components))]
            choices[rank] = (*managers[rank], roots[position])
            selected, failure_reason = attempt(choices)
            if position in selected:
                continue
            local_needed = _required(
                *managers[rank], roots[position], _cube(managers[rank][0], roots[position])
            )
            failure = failure_reason or (
                "the generated configuration did not select this outcome during "
                "concrete preprocessing"
            )
            overridden = sorted(
                {atom.name for atom in local_needed if isinstance(atom, Variable)} & assigned
            )
            if overridden and failure_reason is None:
                failure += (
                    "; the source defines or undefines "
                    + ", ".join(overridden)
                    + ", which overrides the configuration"
                )
            predicates = sorted(
                atom.text
                for atom, value in local_needed.items()
                if value and not isinstance(atom, Variable)
            )
            if predicates:
                failure += (
                    "; it requires "
                    + ", ".join(f"`{text}`" for text in predicates)
                    + " to hold, and cpre does not invent integer values"
                )
            status[position] = BranchOutcomeStatus.NOT_COVERED
            reasons[position] = failure
    except _VerificationLimit as error:
        return BranchCoverageResult(None, None, error.incomplete)
    except AnalysisLimitExceeded as error:
        return BranchCoverageResult(
            None,
            None,
            AnalysisIncomplete(
                ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
                error.resource,
                error.limit,
                error.observed,
                str(error),
            ),
        )

    kept = list(generated)
    for entry in list(kept):
        others: set[int] = set()
        for kept_entry in kept:
            if kept_entry is not entry:
                others |= kept_entry[1]
        if entry[1] <= others:
            kept.remove(entry)

    configurations = tuple(
        CoverageConfiguration(
            item.configuration, item.required, item.dont_care, tuple(sorted(selected))
        )
        for item, selected in kept
    )
    first_cover: dict[int, int] = {}
    for number, (_, selected) in enumerate(kept):
        for position in selected:
            first_cover.setdefault(position, number)
    outcomes = tuple(
        BranchOutcome(
            target.group_line,
            target.branch_line,
            target.directive,
            status[position],
            first_cover.get(position),
            reasons.get(position),
        )
        for position, target in enumerate(targets)
    )
    return BranchCoverageResult(configurations, outcomes)


__all__ = [
    "BranchCoverageResult",
    "BranchOutcome",
    "BranchOutcomeStatus",
    "CoverageConfiguration",
    "cover_branches",
]
