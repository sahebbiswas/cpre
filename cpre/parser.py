"""Internal C/C++ preprocessor conditional directive parser."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ._lines import LINE_BREAK, split_lines
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

DIRECTIVE_LEADING_WHITESPACE = r"[ \t\f\v]"
DIRECTIVE_PREFIX = rf"^{DIRECTIVE_LEADING_WHITESPACE}*#"
DIRECTIVE_RE = re.compile(
    rf"{DIRECTIVE_PREFIX}{DIRECTIVE_LEADING_WHITESPACE}*"
    r"(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b(.*)$"
)


# Preserve quoted replacement tokens such as URLs and comment delimiters. A ``//``
# comment runs on across a backslash-newline, because line splicing comes first.
_COMMENT_SCAN = re.compile(
    r"\b[0-9][A-Za-z0-9_'.]*|"
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r"/\*.*?\*/|//(?:\\(?:\r\n|\r|\n)|[^\r\n])*",
    re.DOTALL,
)


def _blank_comments(source: str) -> tuple[str, frozenset[int]]:
    """Blank out comments, keeping every character position and line break.

    Also returns the zero-based physical lines whose line break lies inside a
    comment. Translation phase 3 turns a comment into a single space, so such a
    line and the one after it belong to the same logical line.
    """
    inside: set[int] = set()
    # ``line`` counts the line breaks before ``cursor``, the end of the last comment
    # that contained one, so each stretch of source is scanned only once.
    line = 0
    cursor = 0

    def replacement(match: re.Match[str]) -> str:
        nonlocal line, cursor
        text = match.group()
        if not text.startswith("/"):
            return text
        if "\n" in text or "\r" in text:
            line += len(LINE_BREAK.findall(source, cursor, match.start()))
            breaks = len(LINE_BREAK.findall(text))
            # The break that ends physical line ``line`` is the comment's first.
            inside.update(range(line, line + breaks))
            line += breaks
            cursor = match.end()
        return "".join(character if character in "\r\n" else " " for character in text)

    return _COMMENT_SCAN.sub(replacement, source), frozenset(inside)


def strip_comments(source: str) -> str:
    return _blank_comments(source)[0]


@dataclass(frozen=True)
class LogicalLine:
    start_line: int
    text: str
    locations: tuple[SourceLocation, ...]


def logical_lines(source: str) -> Iterator[LogicalLine]:
    """Yield logical lines: physical lines joined by backslash-newline or a multi-line comment.

    A comment that spans lines is one space in the logical line, so a directive
    carries on past it, and a ``#`` that follows it is a directive only when the
    comment itself began the line. Locations stay physical, one per character.
    """
    stripped, comment_breaks = _blank_comments(source)
    lines = split_lines(stripped)
    index = 0
    while index < len(lines):
        start = index + 1
        text = lines[index]
        locations = [SourceLocation(start, column) for column in range(1, len(text) + 1)]
        while index + 1 < len(lines):
            if index in comment_breaks:
                # The comment text is already blank, so the next line follows as it is.
                index += 1
                next_line = lines[index]
                text += next_line
                locations += [
                    SourceLocation(index + 1, column) for column in range(1, len(next_line) + 1)
                ]
            elif text.rstrip().endswith("\\"):
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
            else:
                break
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
    "DIRECTIVE_LEADING_WHITESPACE",
    "DIRECTIVE_PREFIX",
    "DIRECTIVE_RE",
    "LogicalLine",
    "directive_expression",
    "logical_lines",
    "parse_source",
    "remainder_location",
    "strip_comments",
]
