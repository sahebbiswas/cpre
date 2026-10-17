"""Lossless, recovery-oriented conditional preprocessor structure.

This module implements the public structural parser exposed as
:func:`cpre.parse_conditionals`. It is intentionally independent of branch
selection, macro configuration, and exact Boolean analysis.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum

from .lexer import CONDITIONAL_DIRECTIVES, Token, lex, scan
from .model import (
    Conjunction,
    Constant,
    DefinedVariable,
    Disjunction,
    Expression,
    Negation,
    Predicate,
    Variable,
)


@dataclass(frozen=True)
class StructuralSourceLocation:
    """Physical source location with a Python string offset."""

    offset: int
    line: int
    column: int


@dataclass(frozen=True)
class StructuralSourceRange:
    """Half-open physical source range."""

    start: StructuralSourceLocation
    end: StructuralSourceLocation

    def text(self, source: str) -> str:
        return source[self.start.offset : self.end.offset]


class StructureDiagnosticCode(str, Enum):
    """Stable machine-readable structural diagnostic categories."""

    MISSING_CONDITION = "missing_condition"
    MALFORMED_MACRO_DIRECTIVE = "malformed_macro_directive"
    TRAILING_DIRECTIVE_TEXT = "trailing_directive_text"
    UNMATCHED_DIRECTIVE = "unmatched_directive"
    DUPLICATE_ELSE = "duplicate_else"
    BRANCH_AFTER_ELSE = "branch_after_else"
    UNTERMINATED_CONDITIONAL = "unterminated_conditional"


@dataclass(frozen=True)
class DirectiveToken:
    """One source-ordered token retained from a conditional directive."""

    text: str
    source_range: StructuralSourceRange


@dataclass(frozen=True)
class StructureDiagnostic:
    """Recoverable structural parse diagnostic."""

    code: StructureDiagnosticCode
    message: str
    source_range: StructuralSourceRange


@dataclass(frozen=True)
class ConditionalDirective:
    """A single conditional-family directive and its exact source ranges."""

    kind: str
    source_range: StructuralSourceRange
    condition_range: StructuralSourceRange | None
    condition_text: str | None
    logical_condition: str | None
    condition: Expression | None
    tokens: tuple[DirectiveToken, ...]


@dataclass(eq=False)
class ConditionalBranch:
    """One branch of a conditional block."""

    directive: ConditionalDirective
    body_range: StructuralSourceRange
    block: ConditionalBlock = field(repr=False)
    children: list[ConditionalBlock] = field(default_factory=list)


@dataclass(eq=False)
class ConditionalBlock:
    """A complete ``#if``/``#elif``/``#else``/``#endif`` block."""

    source_range: StructuralSourceRange
    parent: ConditionalBranch | None = field(default=None, repr=False)
    branches: list[ConditionalBranch] = field(default_factory=list)
    endif: ConditionalDirective | None = None


@dataclass
class ConditionalStructureTree:
    """Lossless conditional structure for one source string."""

    source: str
    blocks: list[ConditionalBlock]
    directives: tuple[ConditionalDirective, ...]
    diagnostics: tuple[StructureDiagnostic, ...]
    filename: str | None = None


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FORMS = CONDITIONAL_DIRECTIVES
_ASSIGNMENTS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=", "<<=", ">>="))


def _tokens(text: str) -> list[Token]:
    """The preprocessing tokens of ``text`` without whitespace, from the shared scanner."""
    return [token for token in scan(text) if token.kind not in ("space", "comment")]


def _atom(text: str) -> Expression:
    if _IDENTIFIER.fullmatch(text):
        return Variable(text)

    match = re.fullmatch(
        r"defined\s*(?:\(\s*([A-Za-z_]\w*)\s*\)|\s+([A-Za-z_]\w*))",
        text,
        re.ASCII,
    )
    if match:
        return DefinedVariable(match[1] or match[2])

    if re.fullmatch(
        r"(?:0[xX][0-9a-fA-F](?:'?[0-9a-fA-F])*|"
        r"0[bB][01](?:'?[01])*|0[0-7](?:'?[0-7])*|"
        r"[1-9][0-9]*(?:'[0-9]+)*|0)[uUlL]*",
        text,
    ):
        digits = text.rstrip("uUlL").replace("'", "")
        base = (
            16
            if digits.lower().startswith("0x")
            else 2
            if digits.lower().startswith("0b")
            else 8
            if len(digits) > 1 and digits.startswith("0")
            else 10
        )
        try:
            return Constant(int(digits, base) != 0)
        except ValueError:
            pass

    return Predicate(text)


def _condition(text: str) -> Expression:
    """Extract safe Boolean structure; retain other syntax as an opaque predicate."""

    tokens = _tokens(text)
    depth = 0
    top: list[Token] = []
    for token in tokens:
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
            if depth < 0:
                return Predicate(text)
        elif depth == 0:
            top.append(token)
    if depth:
        return Predicate(text)

    # These operators bind less tightly than ||. Treating a condition that
    # contains one as Boolean structure would change C semantics.
    if any(token.text in ("?", ":", ",") or token.text in _ASSIGNMENTS for token in top):
        return Predicate(text)

    for operator, node in (("||", Disjunction), ("&&", Conjunction)):
        splits = [token for token in top if token.text == operator]
        if splits:
            parts: list[str] = []
            start = 0
            for token in splits:
                parts.append(text[start : token.start].strip())
                start = token.end
            parts.append(text[start:].strip())
            if all(parts):
                return node(tuple(_condition(part) for part in parts))
            return Predicate(text)

    if tokens and tokens[0].text == "(" and not top:
        depth = 0
        for token_index, token in enumerate(tokens):
            depth += (token.text == "(") - (token.text == ")")
            if depth == 0:
                if token_index == len(tokens) - 1 and text[1:-1].strip():
                    return _condition(text[1:-1].strip())
                break

    if (
        top
        and top[0].text == "!"
        and not any(
            token.text
            in (
                "+",
                "-",
                "*",
                "/",
                "%",
                "<",
                ">",
                "<=",
                ">=",
                "==",
                "!=",
                "&",
                "|",
                "^",
                "<<",
                ">>",
            )
            for token in top[1:]
        )
    ):
        operand = text[1:].strip()
        if operand:
            return Negation(_condition(operand))

    return _atom(text)


def parse_conditionals(
    source: str,
    *,
    filename: str | None = None,
) -> ConditionalStructureTree:
    """Parse conditional directives without selecting a concrete configuration.

    Every returned range uses physical Python string offsets and one-based
    line/column coordinates. Ranges are half-open. Recoverable source-structure
    errors are returned in deterministic source order and do not discard
    unrelated directives or already-recoverable block structure.
    """

    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if filename is not None and not isinstance(filename, str):
        raise TypeError("filename must be a string or None")

    lexed = lex(source)
    starts = lexed.starts

    def location(offset: int) -> StructuralSourceLocation:
        line = bisect_right(starts, offset)
        return StructuralSourceLocation(
            offset=offset,
            line=line,
            column=offset - starts[line - 1] + 1,
        )

    def span(start: int, end: int) -> StructuralSourceRange:
        return StructuralSourceRange(location(start), location(end))

    roots: list[ConditionalBlock] = []
    stack: list[ConditionalBlock] = []
    directives: list[ConditionalDirective] = []
    diagnostics: list[StructureDiagnostic] = []
    seen_else: set[int] = set()

    def diagnostic(
        code: StructureDiagnosticCode,
        message: str,
        where: StructuralSourceRange,
    ) -> None:
        diagnostics.append(StructureDiagnostic(code, message, where))

    for line in lexed.lines:
        head = line.directive
        if head is None or head.name not in _FORMS:
            continue

        kind = head.name
        begin = line.start_offset
        end = line.end_offset
        directive_range = span(begin, end)
        tokens = tuple(
            DirectiveToken(
                token.text,
                span(line.physical_offset(token.start), line.physical_end(token.end)),
            )
            for token in scan(line.text, head.name_end)
            if token.kind not in ("space", "comment")
        )

        condition_range: StructuralSourceRange | None = None
        condition_text: str | None = None
        logical_condition: str | None = None
        expression: Expression | None = None

        if kind in {"else", "endif"}:
            if tokens:
                diagnostic(
                    StructureDiagnosticCode.TRAILING_DIRECTIVE_TEXT,
                    f"#{kind} does not accept trailing tokens",
                    tokens[0].source_range,
                )
        else:
            if tokens:
                condition_range = span(
                    tokens[0].source_range.start.offset,
                    tokens[-1].source_range.end.offset,
                )
                condition_text = condition_range.text(source)
                logical_condition = line.text[head.name_end :].strip()
            else:
                keyword_start = line.physical_offset(head.name_end - len(kind))
                keyword_end = line.physical_end(head.name_end)
                diagnostic(
                    StructureDiagnosticCode.MISSING_CONDITION,
                    f"#{kind} requires a condition",
                    span(keyword_start, keyword_end),
                )

            if tokens and kind in {"ifdef", "ifndef", "elifdef", "elifndef"}:
                if len(tokens) != 1 or not _IDENTIFIER.fullmatch(tokens[0].text):
                    offending = tokens[1] if len(tokens) > 1 else tokens[0]
                    diagnostic(
                        StructureDiagnosticCode.MALFORMED_MACRO_DIRECTIVE,
                        f"#{kind} requires exactly one macro identifier",
                        offending.source_range,
                    )
                else:
                    expression = DefinedVariable(tokens[0].text)
                    if kind in {"ifndef", "elifndef"}:
                        expression = Negation(expression)
            elif tokens and logical_condition is not None:
                try:
                    expression = _condition(logical_condition)
                except RecursionError:
                    expression = Predicate(logical_condition)

        directive = ConditionalDirective(
            kind=kind,
            source_range=directive_range,
            condition_range=condition_range,
            condition_text=condition_text,
            logical_condition=logical_condition,
            condition=expression,
            tokens=tokens,
        )
        directives.append(directive)

        if kind in {"if", "ifdef", "ifndef"}:
            parent = stack[-1].branches[-1] if stack else None
            block = ConditionalBlock(source_range=span(begin, len(source)), parent=parent)
            if parent is None:
                roots.append(block)
            else:
                parent.children.append(block)
            block.branches.append(
                ConditionalBranch(
                    directive=directive,
                    body_range=span(end, len(source)),
                    block=block,
                )
            )
            stack.append(block)
            continue

        if not stack:
            diagnostic(
                StructureDiagnosticCode.UNMATCHED_DIRECTIVE,
                f"#{kind} has no matching opening conditional",
                directive_range,
            )
            continue

        block = stack[-1]
        previous = block.branches[-1]
        previous.body_range = span(previous.body_range.start.offset, begin)

        if kind == "endif":
            block.endif = directive
            block.source_range = span(block.source_range.start.offset, end)
            stack.pop()
            continue

        if id(block) in seen_else:
            code = (
                StructureDiagnosticCode.DUPLICATE_ELSE
                if kind == "else"
                else StructureDiagnosticCode.BRANCH_AFTER_ELSE
            )
            message = "duplicate #else" if kind == "else" else f"#{kind} appears after #else"
            diagnostic(code, message, directive_range)
        if kind == "else":
            seen_else.add(id(block))

        block.branches.append(
            ConditionalBranch(
                directive=directive,
                body_range=span(end, len(source)),
                block=block,
            )
        )

    for block in stack:
        diagnostic(
            StructureDiagnosticCode.UNTERMINATED_CONDITIONAL,
            "conditional block has no matching #endif",
            block.branches[0].directive.source_range,
        )

    diagnostics.sort(key=lambda item: item.source_range.start.offset)
    return ConditionalStructureTree(
        source=source,
        blocks=roots,
        directives=tuple(directives),
        diagnostics=tuple(diagnostics),
        filename=filename,
    )


__all__ = [
    "ConditionalBlock",
    "ConditionalBranch",
    "ConditionalDirective",
    "ConditionalStructureTree",
    "DirectiveToken",
    "StructuralSourceLocation",
    "StructuralSourceRange",
    "StructureDiagnostic",
    "StructureDiagnosticCode",
    "parse_conditionals",
]
