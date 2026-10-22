"""Internal text and JSON rendering for analyzed conditional trees."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from ._style import Style, diff_segments, paint_segments, style_for
from .expressions import expression_predicates, expressions_differ, format_expression
from .model import ConditionalBranch, ConditionalGroup, ConditionalTree


def branch_differs_from_source(branch: ConditionalBranch) -> bool:
    assert branch.analysis is not None
    if branch.expression is None:
        return False
    return expressions_differ(branch.expression, branch.analysis.simplified) or expressions_differ(
        branch.expression, branch.analysis.contextual
    )


def branch_is_notable(branch: ConditionalBranch) -> bool:
    assert branch.analysis is not None
    return branch.analysis.status in {"dead", "redundant"} or branch_differs_from_source(branch)


def analyzed(group: ConditionalGroup) -> bool:
    """Whether ``group`` was analyzed; groups of an incomplete component are not."""
    return all(branch.analysis is not None for branch in group.branches)


@dataclass(frozen=True)
class Visibility:
    branches: frozenset[int]
    groups: frozenset[int]
    detailed_branches: frozenset[int]
    verbose: bool = False


def compute_visibility(tree: ConditionalTree, verbose: bool) -> Visibility:
    visible_branches: set[int] = set()
    visible_groups: set[int] = set()
    detailed_branches: set[int] = set()

    def visit_group(group: ConditionalGroup) -> bool:
        if not analyzed(group):
            return False
        group_visible = False
        for branch in group.branches:
            notable = branch_is_notable(branch)
            child_visible = False
            for child in branch.children:
                child_visible = visit_group(child) or child_visible
            if verbose or notable:
                detailed_branches.add(id(branch))
            if verbose or notable or child_visible:
                visible_branches.add(id(branch))
                group_visible = True
        if group_visible:
            visible_groups.add(id(group))
        return group_visible

    for group in tree.groups:
        visit_group(group)
    return Visibility(
        frozenset(visible_branches),
        frozenset(visible_groups),
        frozenset(detailed_branches),
        verbose,
    )


def branch_dict(branch: ConditionalBranch, visibility: Visibility) -> dict[str, object]:
    assert branch.analysis is not None
    result: dict[str, object] = {
        "directive": branch.directive,
        "line": branch.line,
        "condition": branch.expression_text,
        "status": branch.analysis.status,
        "children": [
            group_dict(group, visibility)
            for group in branch.children
            if id(group) in visibility.groups
        ],
    }
    if id(branch) in visibility.detailed_branches:
        result.update(
            {
                "simplified_condition": (
                    format_expression(branch.analysis.simplified)
                    if branch.analysis.simplified is not None
                    else None
                ),
                "contextual_condition": (
                    format_expression(branch.analysis.contextual)
                    if branch.analysis.contextual is not None
                    else None
                ),
                "effective_condition": format_expression(branch.analysis.effective),
                "reason": branch.analysis.reason,
                "opaque_predicates": (
                    sorted(expression_predicates(branch.expression))
                    if branch.expression is not None
                    else []
                ),
            }
        )
    return result


def group_dict(group: ConditionalGroup, visibility: Visibility) -> dict[str, object]:
    return {
        "line": group.line,
        "end_line": group.end_line,
        "branches": [
            branch_dict(branch, visibility)
            for branch in group.branches
            if id(branch) in visibility.branches
        ],
    }


def tree_to_dict(tree: ConditionalTree, *, verbose: bool = True) -> dict[str, object]:
    visibility = compute_visibility(tree, verbose)
    return {
        "groups": [
            group_dict(group, visibility) for group in tree.groups if id(group) in visibility.groups
        ]
    }


def colored(text: str, color: str, enabled: bool) -> str:
    """``text`` in the named color or role when ``enabled``; the name must be a known role."""
    return Style(color=enabled).paint(text, color)


def branch_color(branch: ConditionalBranch) -> str:
    assert branch.analysis is not None
    if branch.analysis.status == "dead":
        return "red"
    if branch.analysis.status == "redundant":
        return "yellow"
    if branch_differs_from_source(branch):
        return "green"
    return "gray"


# Width of the badge column: wide enough for the longest label used by any report.
BADGE_WIDTH = 10


def badge(style: Style, label: str, role: str) -> str:
    """``label`` as a status badge, padded so the text after it stays aligned."""
    return style.paint(label, role) + " " * max(0, BADGE_WIDTH - len(label))


def branch_kind(branch: ConditionalBranch) -> tuple[str, str]:
    """The badge label and color role of a branch: dead, redundant, simplifiable or ok."""
    assert branch.analysis is not None
    status = branch.analysis.status
    if status == "dead":
        return "DEAD", "dead"
    if status == "redundant":
        return "REDUNDANT", "redundant"
    if branch_differs_from_source(branch):
        return "SIMPLIFY", "simplify"
    return ("ok" if status == "reachable" else status), "ok"


def line_label(line: int, width: int) -> str:
    return f"L{line}".ljust(width)


def _visible_lines(groups: Sequence[ConditionalGroup], visibility: Visibility) -> Iterator[int]:
    for group in groups:
        for branch in group.branches:
            if id(branch) in visibility.branches:
                yield branch.line
                yield from _visible_lines(branch.children, visibility)


def label_width(lines: Iterable[int]) -> int:
    """Width of the ``L<line>`` column for the given line numbers."""
    return 1 + len(str(max(lines, default=0)))


def constant_note(expression: str, suffix: str = "") -> str | None:
    """What a constant result means ("always true"), or ``None`` for any other result."""
    if expression == "1":
        return f"always true{suffix}"
    if expression == "0":
        return f"always false{suffix}"
    return None


def transformation_lines(
    style: Style,
    prefix: str,
    before: str | None,
    afters: Sequence[tuple[str, str | None]],
) -> Iterator[str]:
    """The ``-`` / ``+`` pair that shows a transformation.

    ``afters`` holds each result with an optional dim note. In color the tokens that
    differ from ``before`` are bold; without color the markers alone carry the meaning.
    """
    if not afters:
        return
    first = afters[0][0]
    if before is not None and style.color:
        left, right = diff_segments(before, first)
    else:
        left = [(before, False)] if before is not None else []
        right = [(first, False)]
    if before is not None:
        marker = style.paint("-", "before")
        yield f"{prefix}{marker} {paint_segments(style, left, 'before', 'before_changed')}"
    for index, (text, note) in enumerate(afters):
        if index == 0:
            body = paint_segments(style, right, "after", "after_changed")
        else:
            body = style.paint(text, "after")
        suffix = f"  {style.paint(f'({note})', 'dim')}" if note else ""
        yield f"{prefix}{style.paint('+', 'after')} {body}{suffix}"


def text_lines(
    groups: Sequence[ConditionalGroup],
    visibility: Visibility,
    depth: int = 0,
    *,
    color: bool = False,
    style: Style | None = None,
    width: int | None = None,
) -> Iterator[str]:
    style = style_for(style, color)
    if width is None:
        width = label_width(_visible_lines(groups, visibility))
    guides = style.paint(style.guide * depth, "dim")
    detail = guides + "  "
    for group in groups:
        for branch in group.branches:
            if id(branch) not in visibility.branches:
                continue
            assert branch.analysis is not None
            analysis = branch.analysis
            label, role = branch_kind(branch)
            condition = f" {branch.expression_text}" if branch.expression_text else ""
            yield (
                f"{guides}{line_label(branch.line, width)} {badge(style, label, role)} "
                f"{style.paint('#' + branch.directive, 'bold')}{condition}"
            )
            if id(branch) not in visibility.detailed_branches:
                yield from text_lines(
                    branch.children, visibility, depth + 1, style=style, width=width
                )
                continue
            if analysis.reason:
                yield f"{detail}{style.paint('why', 'dim')}  {analysis.reason}"
            source = format_expression(branch.expression) if branch.expression is not None else None
            shown = source
            if source is not None and analysis.simplified is not None:
                simplified = format_expression(analysis.simplified)
                contextual = format_expression(analysis.contextual or analysis.simplified)
                shown = contextual
                afters: list[tuple[str, str | None]] = []
                if expressions_differ(branch.expression, analysis.simplified):
                    afters.append((simplified, constant_note(simplified)))
                if contextual != simplified:
                    afters.append((contextual, constant_note(contextual, " in context")))
                yield from transformation_lines(style, detail, source, afters)
            predicates = (
                sorted(expression_predicates(branch.expression))
                if branch.expression is not None
                else []
            )
            if predicates:
                yield (
                    f"{detail}{style.paint('opaque', 'dim')}  "
                    f"{style.paint(', '.join(predicates), 'opaque')}"
                )
            if visibility.verbose:
                effective = format_expression(analysis.effective)
                if effective != shown:
                    yield f"{detail}{style.paint('effective', 'dim')}  {style.paint(effective, 'dim')}"
            yield from text_lines(branch.children, visibility, depth + 1, style=style, width=width)


def render_report(
    tree: ConditionalTree,
    *,
    verbose: bool = True,
    color: bool = False,
    style: Style | None = None,
) -> tuple[str, bool]:
    visibility = compute_visibility(tree, verbose)
    report = "\n".join(text_lines(tree.groups, visibility, color=color, style=style))
    if report:
        return report, True
    if tree.groups:
        return "No changed, dead, or redundant conditional directives found.", False
    return "No conditional directives found.", False


def format_report(
    tree: ConditionalTree,
    *,
    verbose: bool = True,
    color: bool = False,
    style: Style | None = None,
) -> str:
    return render_report(tree, verbose=verbose, color=color, style=style)[0]


@dataclass(frozen=True)
class FindingCounts:
    """How many notable entries an analysis produced, by kind.

    ``failing`` is what ``--fail-on-findings`` acts on; ``simplifiable`` counts reachable
    conditions that have a shorter equivalent form, which are reported but never fail a run.
    """

    dead: int = 0
    redundant: int = 0
    simplifiable: int = 0
    macros: int = 0

    def __add__(self, other: FindingCounts) -> FindingCounts:
        return FindingCounts(
            self.dead + other.dead,
            self.redundant + other.redundant,
            self.simplifiable + other.simplifiable,
            self.macros + other.macros,
        )

    @property
    def failing(self) -> int:
        return self.dead + self.redundant + self.macros

    @property
    def total(self) -> int:
        return self.failing + self.simplifiable


def count_findings(tree: ConditionalTree) -> FindingCounts:
    """The dead, redundant and simplifiable conditions in the analyzed part of ``tree``."""
    counts = FindingCounts()
    for group in tree.groups:
        if not analyzed(group):
            continue
        for branch in group.branches:
            assert branch.analysis is not None
            status = branch.analysis.status
            if status == "dead":
                counts += FindingCounts(dead=1)
            elif status == "redundant":
                counts += FindingCounts(redundant=1)
            elif branch_differs_from_source(branch):
                counts += FindingCounts(simplifiable=1)
            counts += count_findings(ConditionalTree(branch.children))
    return counts


def has_findings(tree: ConditionalTree) -> bool:
    for group in tree.groups:
        if not analyzed(group):
            continue
        for branch in group.branches:
            assert branch.analysis is not None
            if branch.analysis.status in {"dead", "redundant"}:
                return True
            if has_findings(ConditionalTree(branch.children)):
                return True
    return False


__all__ = [
    "BADGE_WIDTH",
    "FindingCounts",
    "Visibility",
    "analyzed",
    "badge",
    "branch_differs_from_source",
    "branch_kind",
    "branch_is_notable",
    "colored",
    "compute_visibility",
    "constant_note",
    "count_findings",
    "format_report",
    "has_findings",
    "label_width",
    "line_label",
    "render_report",
    "text_lines",
    "transformation_lines",
    "tree_to_dict",
]
