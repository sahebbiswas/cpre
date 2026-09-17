"""Lossless, recovery-oriented conditional preprocessor structure.

This module implements the public structural parser exposed as
:func:`cpre.parse_conditionals`. It is intentionally independent of branch
selection, macro configuration, and exact Boolean analysis.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum
import re

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
    block: "ConditionalBlock" = field(repr=False)
    children: list["ConditionalBlock"] = field(default_factory=list)


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
_PP_NUMBER = r"(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[A-Za-z0-9_.]|'[A-Za-z0-9_])*"
_NUMBER = re.compile(_PP_NUMBER)
_TOKEN = re.compile(
    r'''[A-Za-z_][A-Za-z0-9_]*|'''
    + _PP_NUMBER
    + r'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|&&|\|\||==|!=|<=|>=|<<|>>|[^\s]'''
)
_FORMS = frozenset(
    ("if", "ifdef", "ifndef", "elif", "elifdef", "elifndef", "else", "endif")
)


def _logical_source(source: str) -> tuple[str, str, list[int]]:
    """Return spliced source, directive-recognition mask, and physical offsets."""

    chars: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(source):
        if source.startswith("\\\r\n", index):
            index += 3
        elif source.startswith("\\\n", index):
            index += 2
        else:
            chars.append(source[index])
            offsets.append(index)
            index += 1

    text = "".join(chars)
    hidden_literals: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        # A preprocessing number owns apostrophe digit separators. Checking it
        # before character literals prevents 1'024 from hiding later source.
        if index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_"):
            number = _NUMBER.match(text, index)
            if number:
                index = number.end()
                continue

        raw = (
            re.match(
                r'(?:u8R|uR|UR|LR|R)"([^ ()\\\t\r\n]{0,16})\(',
                text[index : index + 22],
            )
            if text.startswith(("u8R\"", "uR\"", "UR\"", "LR\"", 'R"'), index)
            else None
        )
        if raw:
            end_marker = ")" + raw[1] + '"'
            end = text.find(end_marker, index + raw.end())
            end = len(text) if end < 0 else end + len(end_marker)
            hidden_literals.append((index, end))
            index = end
            continue

        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
        elif text[index] in "\"'":
            literal_start = index
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            hidden_literals.append((literal_start, min(index, len(text))))
            continue
        else:
            index += 1
            continue

        for masked_index in range(index, end):
            if chars[masked_index] not in "\r\n":
                chars[masked_index] = " "
        index = end

    masked = chars.copy()
    for start, end in hidden_literals:
        for masked_index in range(start, end):
            if masked[masked_index] not in "\r\n":
                masked[masked_index] = "@"
    return "".join(chars), "".join(masked), offsets


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

    tokens = list(_TOKEN.finditer(text))
    depth = 0
    top: list[re.Match[str]] = []
    for token in tokens:
        if token[0] == "(":
            depth += 1
        elif token[0] == ")":
            depth -= 1
            if depth < 0:
                return Predicate(text)
        elif depth == 0:
            top.append(token)
    if depth:
        return Predicate(text)

    # These operators bind less tightly than ||. Treating a condition that
    # contains one as Boolean structure would change C semantics.
    if any(token[0] in ("?", ":", ",", "=") for token in top):
        return Predicate(text)

    for operator, node in (("||", Disjunction), ("&&", Conjunction)):
        splits = [token for token in top if token[0] == operator]
        if splits:
            parts: list[str] = []
            start = 0
            for token in splits:
                parts.append(text[start : token.start()].strip())
                start = token.end()
            parts.append(text[start:].strip())
            if all(parts):
                return node(tuple(_condition(part) for part in parts))
            return Predicate(text)

    if tokens and tokens[0][0] == "(" and not top:
        depth = 0
        for token_index, token in enumerate(tokens):
            depth += (token[0] == "(") - (token[0] == ")")
            if depth == 0:
                if token_index == len(tokens) - 1 and text[1:-1].strip():
                    return _condition(text[1:-1].strip())
                break

    if top and top[0][0] == "!" and not any(
        token[0]
        in ("+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&", "|", "^", "<<", ">>")
        for token in top[1:]
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

    line_starts = [0] + [match.end() for match in re.finditer("\n", source)]

    def location(offset: int) -> StructuralSourceLocation:
        line = bisect_right(line_starts, offset)
        return StructuralSourceLocation(
            offset=offset,
            line=line,
            column=offset - line_starts[line - 1] + 1,
        )

    def span(start: int, end: int) -> StructuralSourceRange:
        return StructuralSourceRange(location(start), location(end))

    logical, recognition_mask, offsets = _logical_source(source)
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

    cursor = 0
    for line_match in re.finditer(r"[^\n]*\n|[^\n]+$", logical):
        line_text = line_match[0]
        line_start = cursor
        cursor += len(line_text)
        masked_line = recognition_mask[line_start:cursor]
        match = re.match(
            r"^[ \t\v\f\r]*#[ \t]*([A-Za-z_][A-Za-z0-9_]*)",
            masked_line,
        )
        if match is None or match[1] not in _FORMS:
            continue

        kind = match[1]
        begin = offsets[line_start - 1] + 1 if line_start else 0
        end = offsets[cursor - 1] + 1 if line_text.endswith("\n") else len(source)
        directive_range = span(begin, end)
        token_matches = tuple(_TOKEN.finditer(line_text, match.end()))
        tokens = tuple(
            DirectiveToken(
                token[0],
                span(
                    offsets[line_start + token.start()],
                    offsets[line_start + token.end() - 1] + 1,
                ),
            )
            for token in token_matches
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
                logical_condition = line_text[match.end() :].strip()
            else:
                keyword_start = offsets[line_start + match.start(1)]
                keyword_end = offsets[line_start + match.end(1) - 1] + 1
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
            message = (
                "duplicate #else"
                if kind == "else"
                else f"#{kind} appears after #else"
            )
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
