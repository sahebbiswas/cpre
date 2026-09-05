"""Internal text and JSON rendering for analyzed conditional trees."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from .expressions import expression_predicates, expressions_differ, format_expression
from .model import ConditionalBranch, ConditionalGroup, ConditionalTree


def branch_differs_from_source(branch: ConditionalBranch) -> bool:
    assert branch.analysis is not None
    if branch.expression is None:
        return False
    return (
        expressions_differ(branch.expression, branch.analysis.simplified)
        or expressions_differ(branch.expression, branch.analysis.contextual)
    )


def branch_is_notable(branch: ConditionalBranch) -> bool:
    assert branch.analysis is not None
    return branch.analysis.status in {"dead", "redundant"} or branch_differs_from_source(branch)


@dataclass(frozen=True)
class Visibility:
    branches: frozenset[int]
    groups: frozenset[int]
    detailed_branches: frozenset[int]


def compute_visibility(tree: ConditionalTree, verbose: bool) -> Visibility:
    visible_branches: set[int] = set()
    visible_groups: set[int] = set()
    detailed_branches: set[int] = set()

    def visit_group(group: ConditionalGroup) -> bool:
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
                    if branch.analysis.simplified is not None else None
                ),
                "contextual_condition": (
                    format_expression(branch.analysis.contextual)
                    if branch.analysis.contextual is not None else None
                ),
                "effective_condition": format_expression(branch.analysis.effective),
                "reason": branch.analysis.reason,
                "opaque_predicates": (
                    sorted(expression_predicates(branch.expression))
                    if branch.expression is not None else []
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
            group_dict(group, visibility)
            for group in tree.groups
            if id(group) in visibility.groups
        ]
    }


_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}
_COLOR_RESET = "\033[0m"


def colored(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS[color]}{text}{_COLOR_RESET}"


def branch_color(branch: ConditionalBranch) -> str:
    assert branch.analysis is not None
    if branch.analysis.status == "dead":
        return "red"
    if branch.analysis.status == "redundant":
        return "yellow"
    if branch_differs_from_source(branch):
        return "green"
    return "gray"


def text_lines(
    groups: Sequence[ConditionalGroup],
    visibility: Visibility,
    depth: int = 0,
    *,
    color: bool = False,
) -> Iterator[str]:
    for group in groups:
        for branch in group.branches:
            if id(branch) not in visibility.branches:
                continue
            assert branch.analysis is not None
            condition = f" {branch.expression_text}" if branch.expression_text else ""
            header = (
                f"{'  ' * depth}{branch.line}: #{branch.directive}{condition} "
                f"[{branch.analysis.status}]"
            )
            yield colored(header, branch_color(branch), color)
            if id(branch) not in visibility.detailed_branches:
                yield from text_lines(branch.children, visibility, depth + 1, color=color)
                continue
            if branch.analysis.reason:
                reason = f"{'  ' * (depth + 1)}reason: {branch.analysis.reason}"
                yield colored(reason, branch_color(branch), color)
            if branch.analysis.simplified is not None:
                simplified = format_expression(branch.analysis.simplified)
                contextual = format_expression(branch.analysis.contextual or branch.analysis.simplified)
                simplified_line = f"{'  ' * (depth + 1)}simplified: {simplified}"
                simplified_color = (
                    "green" if expressions_differ(branch.expression, branch.analysis.simplified) else "gray"
                )
                yield colored(simplified_line, simplified_color, color)
                if contextual != simplified:
                    contextual_line = f"{'  ' * (depth + 1)}in context: {contextual}"
                    yield colored(contextual_line, "green", color)
                predicates = sorted(expression_predicates(branch.expression))
                if predicates:
                    opaque = f"{'  ' * (depth + 1)}opaque: {', '.join(predicates)}"
                    yield colored(opaque, "cyan", color)
            effective = f"{'  ' * (depth + 1)}effective: {format_expression(branch.analysis.effective)}"
            yield colored(effective, "gray", color)
            yield from text_lines(branch.children, visibility, depth + 1, color=color)


def render_report(
    tree: ConditionalTree, *, verbose: bool = True, color: bool = False
) -> tuple[str, bool]:
    visibility = compute_visibility(tree, verbose)
    report = "\n".join(text_lines(tree.groups, visibility, color=color))
    if report:
        return report, True
    if tree.groups:
        return "No changed, dead, or redundant conditional directives found.", False
    return "No conditional directives found.", False


def format_report(tree: ConditionalTree, *, verbose: bool = True, color: bool = False) -> str:
    return render_report(tree, verbose=verbose, color=color)[0]


def has_findings(tree: ConditionalTree) -> bool:
    for group in tree.groups:
        for branch in group.branches:
            assert branch.analysis is not None
            if branch.analysis.status in {"dead", "redundant"}:
                return True
            if has_findings(ConditionalTree(branch.children)):
                return True
    return False


__all__ = [
    "Visibility", "branch_differs_from_source", "branch_is_notable", "colored",
    "compute_visibility", "format_report", "has_findings", "render_report", "tree_to_dict"
]
