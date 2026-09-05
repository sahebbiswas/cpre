"""Internal C/C++ preprocessor conditional directive parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

from .expressions import ExpressionParser
from .model import (
    ConditionalBranch,
    ConditionalGroup,
    ConditionalTree,
    DefinedVariable,
    DirectiveStructureError,
    Expression,
    ExpressionSyntaxError,
    Negation,
    SourceLocation,
    Variable,
)

DIRECTIVE_RE = re.compile(
    r"^\s*#\s*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b(.*)$"
)


def strip_comments(source: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        return "".join("\n" if character == "\n" else " " for character in match.group())
    source = re.sub(r"/\*.*?\*/", replacement, source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", replacement, source)


@dataclass(frozen=True)
class LogicalLine:
    start_line: int
    text: str
    locations: tuple[SourceLocation, ...]


def logical_lines(source: str) -> Iterator[LogicalLine]:
    lines = strip_comments(source).splitlines()
    index = 0
    while index < len(lines):
        start = index + 1
        text = lines[index]
        locations = [SourceLocation(start, column) for column in range(1, len(text) + 1)]
        while text.rstrip().endswith("\\") and index + 1 < len(lines):
            trimmed = text.rstrip()
            prefix = trimmed[:-1]
            continuation_location = locations[len(trimmed) - 1]
            index += 1
            next_line = lines[index]
            leading_space_count = len(next_line) - len(next_line.lstrip())
            continuation = next_line.lstrip()
            text = prefix + " " + continuation
            locations = (
                locations[: len(prefix)]
                + [continuation_location]
                + [
                    SourceLocation(index + 1, column)
                    for column in range(leading_space_count + 1, len(next_line) + 1)
                ]
            )
        yield LogicalLine(start, text, tuple(locations))
        index += 1


def remainder_location(
    remainder: str,
    line: int,
    locations: Sequence[SourceLocation] | None,
) -> SourceLocation:
    leading_space_count = len(remainder) - len(remainder.lstrip())
    if locations is not None and leading_space_count < len(locations):
        return locations[leading_space_count]
    if locations:
        last = locations[-1]
        return SourceLocation(last.line or line, last.column + 1)
    return SourceLocation(line, 1)


def directive_expression(
    kind: str,
    remainder: str,
    line: int,
    locations: Sequence[SourceLocation] | None = None,
    *,
    distinguish_defined: bool = False,
) -> tuple[str, Expression]:
    text = remainder.strip()
    location = remainder_location(remainder, line, locations)
    if locations is not None:
        leading_space_count = len(remainder) - len(remainder.lstrip())
        locations = locations[leading_space_count : leading_space_count + len(text)]
    if kind in {"ifdef", "ifndef", "elifdef", "elifndef"}:
        if not re.fullmatch(r"[A-Za-z_]\w*", text):
            raise ExpressionSyntaxError(
                f"#{kind} expects exactly one macro name",
                code="malformed_macro_directive",
                location=location,
            )
        atom = DefinedVariable(text) if distinguish_defined else Variable(text)
        expression: Expression = atom
        if kind in {"ifndef", "elifndef"}:
            expression = Negation(expression)
        return text, expression
    try:
        return text, ExpressionParser(
            text,
            locations,
            distinguish_defined=distinguish_defined,
        ).parse()
    except ExpressionSyntaxError as error:
        if error.location is not None:
            raise
        raise ExpressionSyntaxError(
            error.message,
            code=error.code or "expression_syntax",
            location=location,
        ) from error


def parse_source(source: str, *, distinguish_defined: bool = False) -> ConditionalTree:
    tree = ConditionalTree()
    stack: list[tuple[ConditionalGroup, ConditionalBranch]] = []
    for logical_line in logical_lines(source):
        line = logical_line.start_line
        match = DIRECTIVE_RE.match(logical_line.text)
        if not match:
            continue
        kind, remainder = match.group(1), match.group(2)
        remainder_locations = logical_line.locations[match.start(2) :]
        if kind in {"if", "ifdef", "ifndef"}:
            expression_text, expression = directive_expression(
                kind,
                remainder,
                line,
                remainder_locations,
                distinguish_defined=distinguish_defined,
            )
            group = ConditionalGroup(line)
            branch = ConditionalBranch(kind, line, expression_text, expression)
            group.branches.append(branch)
            if stack:
                stack[-1][1].children.append(group)
            else:
                tree.groups.append(group)
            stack.append((group, branch))
            continue
        if not stack:
            raise DirectiveStructureError(
                f"#{kind} has no matching #if",
                code="unmatched_directive",
                location=SourceLocation(line, 1),
            )
        group, current = stack[-1]
        if kind in {"elif", "elifdef", "elifndef"}:
            if current.directive == "else":
                raise DirectiveStructureError(
                    f"#{kind} appears after #else",
                    code="misplaced_directive",
                    location=SourceLocation(line, 1),
                )
            expression_text, expression = directive_expression(
                kind,
                remainder,
                line,
                remainder_locations,
                distinguish_defined=distinguish_defined,
            )
            branch = ConditionalBranch(kind, line, expression_text, expression)
            group.branches.append(branch)
            stack[-1] = (group, branch)
        elif kind == "else":
            if remainder.strip():
                raise DirectiveStructureError(
                    "unexpected text after #else",
                    code="trailing_directive_text",
                    location=remainder_location(remainder, line, remainder_locations),
                )
            if current.directive == "else":
                raise DirectiveStructureError(
                    "duplicate #else",
                    code="misplaced_directive",
                    location=SourceLocation(line, 1),
                )
            branch = ConditionalBranch(kind, line, None, None)
            group.branches.append(branch)
            stack[-1] = (group, branch)
        else:
            if remainder.strip():
                raise DirectiveStructureError(
                    "unexpected text after #endif",
                    code="trailing_directive_text",
                    location=remainder_location(remainder, line, remainder_locations),
                )
            group.end_line = line
            stack.pop()
    if stack:
        group, _ = stack[-1]
        raise DirectiveStructureError(
            "#if has no matching #endif",
            code="unterminated_conditional",
            location=SourceLocation(group.line, 1),
        )
    return tree


__all__ = [
    "DIRECTIVE_RE", "LogicalLine", "directive_expression", "logical_lines",
    "parse_source", "remainder_location", "strip_comments"
]
