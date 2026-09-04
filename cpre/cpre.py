#!/usr/bin/env python3
"""Analyze Boolean conditions in C/C++ preprocessor conditional blocks.

This intentionally does not preprocess or parse C/C++ source code. It only
recognizes conditional directives, treats identifiers as Boolean flags, and
preserves value-bearing expressions as opaque Boolean predicates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True)
class Constant:
    value: bool


@dataclass(frozen=True)
class Variable:
    name: str


@dataclass(frozen=True)
class Predicate:
    """A value-bearing expression treated as one opaque Boolean fact."""

    text: str


@dataclass(frozen=True)
class Negation:
    operand: Expression


@dataclass(frozen=True)
class Conjunction:
    operands: tuple[Expression, ...]


@dataclass(frozen=True)
class Disjunction:
    operands: tuple[Expression, ...]


# Runtime aliases cannot use ``|`` until Python 3.10. Keep the documented
# Python 3.9 support while using modern union syntax for postponed annotations.
BooleanAtom = typing.Union[Variable, Predicate]
Expression = typing.Union[
    Constant, Variable, Predicate, Negation, Conjunction, Disjunction
]
TRUE = Constant(True)
FALSE = Constant(False)


@dataclass(frozen=True)
class _SourceLocation:
    line: int | None
    column: int


def _format_location(location: _SourceLocation) -> str:
    if location.line is None:
        return f"column {location.column}"
    return f"line {location.line}, column {location.column}"


class ConditionError(ValueError):
    """Base class for input errors reported by the analyzer."""


class ExpressionSyntaxError(ConditionError):
    """Raised for a malformed Boolean expression."""

    def __init__(
        self, message: str, *, location: _SourceLocation | None = None
    ) -> None:
        self.message = message
        self.location = location
        if location is not None:
            message = f"{message} at {_format_location(location)}"
        super().__init__(message)


class DirectiveStructureError(ConditionError):
    """Raised for unmatched or misplaced conditional directives."""


_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<and>&&)|(?P<or>\|\|)|(?P<not>!(?!=))|"
    r"""(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|"""
    r"(?P<lparen>\()|(?P<rparen>\))|"
    r"(?P<number>0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*)|"
    r"(?P<identifier>[A-Za-z_]\w*)|"
    r"(?P<other>[^A-Za-z0-9_\s()]+)"
    r")"
)


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    location: _SourceLocation


def _tokens(
    text: str, locations: Sequence[_SourceLocation] | None = None
) -> list[_Token]:
    if locations is not None and len(locations) != len(text):
        raise ValueError("source locations must correspond to every input character")

    def location_at(offset: int) -> _SourceLocation:
        if locations is None:
            return _SourceLocation(None, offset + 1)
        return locations[offset]

    result: list[_Token] = []
    offset = 0
    while offset < len(text):
        match = _TOKEN_RE.match(text, offset)
        if not match:
            if text[offset:].strip():
                raise ExpressionSyntaxError(
                    f"unsupported input: {text[offset:]!r}",
                    location=location_at(offset),
                )
            break
        kind = match.lastgroup
        assert kind is not None
        token_text = match.group(kind)
        result.append(_Token(kind, token_text, location_at(match.start(kind))))
        offset = match.end()
    return result


class _ExpressionParser:
    def __init__(
        self, text: str, locations: Sequence[_SourceLocation] | None = None
    ):
        self.text = text
        self.tokens = _tokens(text, locations)

    def parse(self) -> Expression:
        if not self.tokens:
            raise ExpressionSyntaxError("expected a Boolean expression")
        return self._parse_tokens(self.tokens)

    def _parse_tokens(self, tokens: Sequence[_Token]) -> Expression:
        if not tokens:
            raise ExpressionSyntaxError("expected an operand")
        self._validate_parentheses(tokens)

        # The conditional and comma operators bind more weakly than ||. Their
        # complete semantics are outside this tool's scope, so preserve the
        # whole expression as one predicate rather than splitting it wrongly.
        if self._has_top_level_other(tokens, {"?", ","}):
            return Predicate(self._normalize_predicate(tokens))

        parts = self._split_top_level(tokens, "or")
        if len(parts) > 1:
            return Disjunction(tuple(self._parse_tokens(part) for part in parts))
        parts = self._split_top_level(tokens, "and")
        if len(parts) > 1:
            return Conjunction(tuple(self._parse_tokens(part) for part in parts))

        if self._is_wrapped(tokens):
            return self._parse_tokens(tokens[1:-1])

        if tokens[0].kind == "not":
            operand_tokens = tokens[1:]
            operand = self._parse_tokens(operand_tokens)
            if isinstance(operand, Predicate) and not self._is_wrapped(operand_tokens):
                return Predicate(self._normalize_predicate(tokens))
            return Negation(operand)

        defined = self._parse_defined(tokens)
        if defined is not None:
            return defined
        if len(tokens) == 1 and tokens[0].kind == "identifier":
            return Variable(tokens[0].text)
        if len(tokens) == 1 and tokens[0].kind == "number":
            return self._parse_number(tokens[0])
        return Predicate(self._normalize_predicate(tokens))

    def _validate_parentheses(self, tokens: Sequence[_Token]) -> None:
        openings: list[_Token] = []
        for token in tokens:
            if token.kind == "lparen":
                openings.append(token)
            elif token.kind == "rparen":
                if not openings:
                    raise ExpressionSyntaxError(
                        "unexpected ')'", location=token.location
                    )
                openings.pop()
        if openings:
            opening = openings[-1]
            raise ExpressionSyntaxError(
                "expected ')' before end of expression; unmatched '('",
                location=opening.location,
            )

    @staticmethod
    def _is_wrapped(tokens: Sequence[_Token]) -> bool:
        if len(tokens) < 2 or tokens[0].kind != "lparen":
            return False
        depth = 0
        for index, token in enumerate(tokens):
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
                if depth == 0:
                    return index == len(tokens) - 1
        return False

    @staticmethod
    def _split_top_level(
        tokens: Sequence[_Token], operator: str
    ) -> list[Sequence[_Token]]:
        depth = 0
        start = 0
        parts: list[Sequence[_Token]] = []
        for index, token in enumerate(tokens):
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
            elif depth == 0 and token.kind == operator:
                if index == start:
                    raise ExpressionSyntaxError(
                        "expected an operand", location=token.location
                    )
                parts.append(tokens[start:index])
                start = index + 1
        if parts:
            if start == len(tokens):
                token = tokens[-1]
                raise ExpressionSyntaxError(
                    f"expected an operand after {token.text!r}",
                    location=token.location,
                )
            parts.append(tokens[start:])
        return parts or [tokens]

    @staticmethod
    def _has_top_level_other(tokens: Sequence[_Token], operators: set[str]) -> bool:
        depth = 0
        for token in tokens:
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
            elif depth == 0 and token.kind == "other":
                if any(operator in token.text for operator in operators):
                    return True
        return False

    @staticmethod
    def _parse_defined(tokens: Sequence[_Token]) -> Expression | None:
        if not tokens or tokens[0].kind != "identifier":
            return None
        if tokens[0].text != "defined":
            return None
        if len(tokens) == 2 and tokens[1].kind == "identifier":
            return Variable(tokens[1].text)
        if (
            len(tokens) == 4
            and tokens[1].kind == "lparen"
            and tokens[2].kind == "identifier"
            and tokens[3].kind == "rparen"
        ):
            return Variable(tokens[2].text)
        return None

    @staticmethod
    def _parse_number(token: _Token) -> Constant:
        digits = re.sub(r"[uUlL]+$", "", token.text)
        if digits.lower().startswith("0x"):
            base = 16
        elif len(digits) > 1 and digits.startswith("0"):
            base = 8
        else:
            base = 10
        try:
            return Constant(int(digits, base) != 0)
        except ValueError as error:
            raise ExpressionSyntaxError(
                f"invalid integer {token.text!r}", location=token.location
            ) from error

    @staticmethod
    def _normalize_predicate(tokens: Sequence[_Token]) -> str:
        parts: list[str] = []
        previous: _Token | None = None
        for token in tokens:
            needs_space = previous is not None
            if token.kind == "rparen" or (
                previous is not None and previous.kind == "lparen"
            ):
                needs_space = False
            if (
                token.kind == "lparen"
                and previous is not None
                and previous.kind == "identifier"
            ):
                needs_space = False
            if needs_space:
                parts.append(" ")
            parts.append(token.text)
            previous = token
        return "".join(parts)


def parse_expression(text: str) -> Expression:
    """Parse Boolean structure, preserving value expressions as predicates."""

    return _ExpressionParser(text).parse()


def _sort_key(expression: Expression) -> str:
    return format_expression(expression)


def negate(expression: Expression) -> Expression:
    expression = simplify(expression)
    if isinstance(expression, Constant):
        return Constant(not expression.value)
    if isinstance(expression, Negation):
        return expression.operand
    return Negation(expression)


def conjunction(*expressions: Expression) -> Expression:
    operands: list[Expression] = []
    for expression in expressions:
        expression = simplify(expression)
        if expression == FALSE:
            return FALSE
        if expression == TRUE:
            continue
        if isinstance(expression, Conjunction):
            operands.extend(expression.operands)
        else:
            operands.append(expression)
    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return FALSE
    filtered = [
        operand
        for operand in unique
        if not (
            isinstance(operand, Disjunction)
            and any(term in unique for term in operand.operands)
        )
    ]
    if not filtered:
        return TRUE
    if len(filtered) == 1:
        return filtered[0]
    return Conjunction(tuple(sorted(filtered, key=_sort_key)))


def disjunction(*expressions: Expression) -> Expression:
    operands: list[Expression] = []
    for expression in expressions:
        expression = simplify(expression)
        if expression == TRUE:
            return TRUE
        if expression == FALSE:
            continue
        if isinstance(expression, Disjunction):
            operands.extend(expression.operands)
        else:
            operands.append(expression)
    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return TRUE
    filtered = [
        operand
        for operand in unique
        if not (
            isinstance(operand, Conjunction)
            and any(term in unique for term in operand.operands)
        )
    ]
    if not filtered:
        return FALSE
    if len(filtered) == 1:
        return filtered[0]
    return Disjunction(tuple(sorted(filtered, key=_sort_key)))


def simplify(expression: Expression) -> Expression:
    """Apply Boolean identities, including complements and absorption."""

    if isinstance(expression, (Constant, Variable, Predicate)):
        return expression
    if isinstance(expression, Negation):
        return negate(expression.operand)
    if isinstance(expression, Conjunction):
        return conjunction(*expression.operands)
    return disjunction(*expression.operands)


def _precedence(expression: Expression) -> int:
    if isinstance(expression, Predicate):
        # Predicate text may contain lower-precedence C operators. Treat it as
        # low precedence so embedding it in Boolean output adds parentheses.
        return 0
    if isinstance(expression, Disjunction):
        return 1
    if isinstance(expression, Conjunction):
        return 2
    if isinstance(expression, Negation):
        return 3
    return 4


def format_expression(expression: Expression, parent_precedence: int = 0) -> str:
    """Format an expression using conventional preprocessor operators."""

    if isinstance(expression, Constant):
        text = "1" if expression.value else "0"
    elif isinstance(expression, Variable):
        text = expression.name
    elif isinstance(expression, Predicate):
        text = expression.text
    elif isinstance(expression, Negation):
        text = f"!{format_expression(expression.operand, _precedence(expression))}"
    else:
        operator = " && " if isinstance(expression, Conjunction) else " || "
        precedence = _precedence(expression)
        text = operator.join(
            format_expression(item, precedence) for item in expression.operands
        )
    return f"({text})" if _precedence(expression) < parent_precedence else text


def expression_atoms(expression: Expression) -> set[BooleanAtom]:
    """Return Boolean flags and opaque predicates referenced by an expression."""

    if isinstance(expression, Constant):
        return set()
    if isinstance(expression, (Variable, Predicate)):
        return {expression}
    if isinstance(expression, Negation):
        return expression_atoms(expression.operand)
    result: set[BooleanAtom] = set()
    for operand in expression.operands:
        result.update(expression_atoms(operand))
    return result


def _expression_atoms_in_order(expression: Expression) -> Iterator[BooleanAtom]:
    """Yield atoms in their first-appearance order within an expression."""

    if isinstance(expression, (Variable, Predicate)):
        yield expression
    elif isinstance(expression, Negation):
        yield from _expression_atoms_in_order(expression.operand)
    elif isinstance(expression, (Conjunction, Disjunction)):
        for operand in expression.operands:
            yield from _expression_atoms_in_order(operand)


def expression_predicates(expression: Expression) -> set[str]:
    """Return the opaque value-bearing predicates in an expression."""

    return {
        atom.text
        for atom in expression_atoms(expression)
        if isinstance(atom, Predicate)
    }


class _BDD:
    """Small dependency-free ROBDD engine used for exact logical queries."""

    def __init__(self, atoms: Iterable[BooleanAtom]):
        # Callers provide atoms in source order. Keeping flags that occur near
        # one another in an expression adjacent often avoids the exponential
        # growth caused by a purely alphabetical order.
        ordered_atoms = list(dict.fromkeys(atoms))
        self.order = {atom: index for index, atom in enumerate(ordered_atoms)}
        self.atoms = ordered_atoms
        self.nodes: list[tuple[int, int, int] | None] = [None, None]
        self.unique: dict[tuple[int, int, int], int] = {}
        self._apply_cache: dict[tuple[str, int, int], int] = {}
        self._not_cache: dict[int, int] = {0: 1, 1: 0}
        self._build_cache: dict[Expression, int] = {}
        self._expression_cache: dict[int, Expression] = {0: FALSE, 1: TRUE}

    def node_count(self, root: int) -> int:
        """Return the number of non-terminal nodes reachable from ``root``."""

        reachable: set[int] = set()
        pending = [root]
        while pending:
            node = pending.pop()
            if node < 2 or node in reachable:
                continue
            reachable.add(node)
            item = self.nodes[node]
            assert item is not None
            _, low, high = item
            pending.extend((low, high))
        return len(reachable)

    def _node(self, variable: int, low: int, high: int) -> int:
        if low == high:
            return low
        key = (variable, low, high)
        if key not in self.unique:
            self.unique[key] = len(self.nodes)
            self.nodes.append(key)
        return self.unique[key]

    def build(self, expression: Expression) -> int:
        expression = simplify(expression)
        cached = self._build_cache.get(expression)
        if cached is not None:
            return cached
        if isinstance(expression, Constant):
            result = int(expression.value)
        elif isinstance(expression, (Variable, Predicate)):
            result = self._node(self.order[expression], 0, 1)
        elif isinstance(expression, Negation):
            result = self.negate(self.build(expression.operand))
        elif isinstance(expression, Conjunction):
            result = 1
            for operand in expression.operands:
                result = self.apply("and", result, self.build(operand))
        else:
            result = 0
            for operand in expression.operands:
                result = self.apply("or", result, self.build(operand))
        self._build_cache[expression] = result
        return result

    def negate(self, node: int) -> int:
        if node in self._not_cache:
            return self._not_cache[node]
        item = self.nodes[node]
        assert item is not None
        variable, low, high = item
        result = self._node(variable, self.negate(low), self.negate(high))
        self._not_cache[node] = result
        return result

    def apply(self, operation: str, left: int, right: int) -> int:
        if left > right:
            left, right = right, left
        key = (operation, left, right)
        if key in self._apply_cache:
            return self._apply_cache[key]
        if operation == "and":
            if left == 0 or right == 0:
                return 0
            if left == 1:
                return right
            if left == right:
                return left
        elif operation == "or":
            if left == 1 or right == 1:
                return 1
            if left == 0:
                return right
            if left == right:
                return left
        else:
            raise ValueError(f"unknown BDD operation: {operation}")

        left_node = self.nodes[left]
        right_node = self.nodes[right]
        assert left_node is not None and right_node is not None
        variable = min(left_node[0], right_node[0])
        left_low, left_high = (
            (left_node[1], left_node[2]) if left_node[0] == variable else (left, left)
        )
        right_low, right_high = (
            (right_node[1], right_node[2])
            if right_node[0] == variable
            else (right, right)
        )
        result = self._node(
            variable,
            self.apply(operation, left_low, right_low),
            self.apply(operation, left_high, right_high),
        )
        self._apply_cache[key] = result
        return result

    def satisfiable(self, expression: Expression) -> bool:
        return self.build(expression) != 0

    def to_expression(self, node: int) -> Expression:
        """Convert a BDD to a reasonably compact equivalent expression."""

        cached = self._expression_cache.get(node)
        if cached is not None:
            return cached
        item = self.nodes[node]
        assert item is not None
        variable_index, low_node, high_node = item
        variable = self.atoms[variable_index]
        low = self.to_expression(low_node)
        high = self.to_expression(high_node)
        if low == FALSE:
            result = conjunction(variable, high)
        elif high == FALSE:
            result = conjunction(negate(variable), low)
        elif low == TRUE:
            result = disjunction(negate(variable), high)
        elif high == TRUE:
            result = disjunction(variable, low)
        else:
            result = disjunction(
                conjunction(negate(variable), low),
                conjunction(variable, high),
            )
        self._expression_cache[node] = result
        return result

    def equivalent_under(
        self, context: Expression, left: Expression, right: Expression
    ) -> bool:
        left_node = self.build(left)
        right_node = self.build(right)
        difference = self.apply(
            "or",
            self.apply("and", left_node, self.negate(right_node)),
            self.apply("and", self.negate(left_node), right_node),
        )
        return self.apply("and", self.build(context), difference) == 0


def _expression_size(expression: Expression) -> int:
    if isinstance(expression, (Constant, Variable, Predicate)):
        return 1
    if isinstance(expression, Negation):
        return 1 + _expression_size(expression.operand)
    return 1 + sum(_expression_size(operand) for operand in expression.operands)


def exact_simplify(expression: Expression, bdd: _BDD) -> Expression:
    """Return the smaller of algebraic and canonical exact simplifications."""

    algebraic = simplify(expression)
    canonical = bdd.to_expression(bdd.build(algebraic))
    if _expression_size(canonical) < _expression_size(algebraic):
        return canonical
    return algebraic


def simplify_under(
    expression: Expression, context: Expression, bdd: _BDD
) -> Expression:
    """Simplify an expression using facts guaranteed by an enclosing context."""

    expression = simplify(expression)
    if bdd.equivalent_under(context, expression, TRUE):
        return TRUE
    if bdd.equivalent_under(context, expression, FALSE):
        return FALSE
    if isinstance(expression, Negation):
        return negate(simplify_under(expression.operand, context, bdd))
    if not isinstance(expression, (Conjunction, Disjunction)):
        return expression

    operands = [simplify_under(item, context, bdd) for item in expression.operands]
    constructor = conjunction if isinstance(expression, Conjunction) else disjunction
    candidate = constructor(*operands)
    if not isinstance(candidate, (Conjunction, Disjunction)):
        return candidate

    operands = list(candidate.operands)
    changed = True
    while changed and len(operands) > 1:
        changed = False
        for index in range(len(operands)):
            trial = constructor(*(operands[:index] + operands[index + 1 :]))
            if bdd.equivalent_under(context, candidate, trial):
                candidate = trial
                operands.pop(index)
                changed = True
                break
    return candidate


@dataclass
class ConditionalBranch:
    directive: str
    line: int
    expression_text: str | None
    expression: Expression | None
    children: list["ConditionalGroup"] = field(default_factory=list)
    analysis: BranchAnalysis | None = None


@dataclass
class ConditionalGroup:
    line: int
    end_line: int | None = None
    branches: list[ConditionalBranch] = field(default_factory=list)


@dataclass
class ConditionalTree:
    groups: list[ConditionalGroup] = field(default_factory=list)


@dataclass(frozen=True)
class BranchAnalysis:
    status: str
    simplified: Expression | None
    contextual: Expression | None
    effective: Expression
    reason: str | None = None


_DIRECTIVE_RE = re.compile(
    r"^\s*#\s*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b(.*)$"
)


def _strip_comments(source: str) -> str:
    """Remove comments while preserving source line and column positions."""

    def replacement(match: re.Match[str]) -> str:
        return "".join(
            "\n" if character == "\n" else " " for character in match.group()
        )

    source = re.sub(r"/\*.*?\*/", replacement, source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", replacement, source)


@dataclass(frozen=True)
class _LogicalLine:
    start_line: int
    text: str
    locations: tuple[_SourceLocation, ...]


def _logical_lines(source: str) -> Iterator[_LogicalLine]:
    lines = _strip_comments(source).splitlines()
    index = 0
    while index < len(lines):
        start = index + 1
        text = lines[index]
        locations = [
            _SourceLocation(start, column) for column in range(1, len(text) + 1)
        ]
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
                    _SourceLocation(index + 1, column)
                    for column in range(
                        leading_space_count + 1, len(next_line) + 1
                    )
                ]
            )
        yield _LogicalLine(start, text, tuple(locations))
        index += 1


def _directive_expression(
    kind: str,
    remainder: str,
    line: int,
    locations: Sequence[_SourceLocation] | None = None,
) -> tuple[str, Expression]:
    text = remainder.strip()
    if locations is not None:
        leading_space_count = len(remainder) - len(remainder.lstrip())
        locations = locations[
            leading_space_count : leading_space_count + len(text)
        ]
    if kind in {"ifdef", "ifndef", "elifdef", "elifndef"}:
        if not re.fullmatch(r"[A-Za-z_]\w*", text):
            raise ExpressionSyntaxError(
                f"line {line}: #{kind} expects exactly one macro name"
            )
        expression: Expression = Variable(text)
        if kind in {"ifndef", "elifndef"}:
            expression = Negation(expression)
        return text, expression
    try:
        return text, _ExpressionParser(text, locations).parse()
    except ExpressionSyntaxError as error:
        if error.location is not None:
            raise
        raise ExpressionSyntaxError(f"line {line}: {error}") from error


def parse_source(source: str) -> ConditionalTree:
    """Build a tree containing only conditional preprocessor directives."""

    tree = ConditionalTree()
    stack: list[tuple[ConditionalGroup, ConditionalBranch]] = []

    for logical_line in _logical_lines(source):
        line = logical_line.start_line
        text = logical_line.text
        match = _DIRECTIVE_RE.match(text)
        if not match:
            continue
        kind, remainder = match.group(1), match.group(2)
        remainder_locations = logical_line.locations[match.start(2) :]

        if kind in {"if", "ifdef", "ifndef"}:
            expression_text, expression = _directive_expression(
                kind, remainder, line, remainder_locations
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
            raise DirectiveStructureError(f"line {line}: #{kind} has no matching #if")
        group, current = stack[-1]

        if kind in {"elif", "elifdef", "elifndef"}:
            if current.directive == "else":
                raise DirectiveStructureError(
                    f"line {line}: #{kind} appears after #else"
                )
            expression_text, expression = _directive_expression(
                kind, remainder, line, remainder_locations
            )
            branch = ConditionalBranch(kind, line, expression_text, expression)
            group.branches.append(branch)
            stack[-1] = (group, branch)
        elif kind == "else":
            if remainder.strip():
                raise DirectiveStructureError(
                    f"line {line}: unexpected text after #else"
                )
            if current.directive == "else":
                raise DirectiveStructureError(f"line {line}: duplicate #else")
            branch = ConditionalBranch(kind, line, None, None)
            group.branches.append(branch)
            stack[-1] = (group, branch)
        else:
            if remainder.strip():
                raise DirectiveStructureError(
                    f"line {line}: unexpected text after #endif"
                )
            group.end_line = line
            stack.pop()

    if stack:
        group, _ = stack[-1]
        raise DirectiveStructureError(f"line {group.line}: #if has no matching #endif")
    return tree


def _tree_expressions(groups: Sequence[ConditionalGroup]) -> Iterator[Expression]:
    for group in groups:
        for branch in group.branches:
            if branch.expression is not None:
                yield branch.expression
            yield from _tree_expressions(branch.children)


def analyze_tree(tree: ConditionalTree) -> ConditionalTree:
    """Annotate each branch with reachability and simplification results."""

    atoms: list[BooleanAtom] = []
    seen_atoms: set[BooleanAtom] = set()
    for expression in _tree_expressions(tree.groups):
        for atom in _expression_atoms_in_order(expression):
            if atom not in seen_atoms:
                seen_atoms.add(atom)
                atoms.append(atom)
    bdd = _BDD(atoms)

    def analyze_groups(groups: Sequence[ConditionalGroup], parent: Expression) -> None:
        for group in groups:
            covered: Expression = FALSE
            for branch in group.branches:
                available = conjunction(parent, negate(covered))
                condition = branch.expression if branch.expression is not None else TRUE
                effective = conjunction(available, condition)
                simplified = (
                    exact_simplify(condition, bdd)
                    if branch.expression is not None
                    else None
                )
                contextual = (
                    simplify_under(condition, available, bdd)
                    if branch.expression is not None and bdd.satisfiable(available)
                    else simplified
                )

                if not bdd.satisfiable(parent):
                    status = "dead"
                    reason = "enclosing branch is unreachable"
                elif not bdd.satisfiable(available):
                    status = "dead"
                    reason = "earlier branch conditions cover every remaining case"
                elif not bdd.satisfiable(effective):
                    status = "dead"
                    reason = "condition contradicts its parent or earlier branches"
                elif branch.expression is not None and contextual == TRUE:
                    status = "redundant"
                    reason = "condition is always true in this branch context"
                else:
                    status = "reachable"
                    reason = None

                branch.analysis = BranchAnalysis(
                    status=status,
                    simplified=simplified,
                    contextual=contextual,
                    effective=simplify(effective),
                    reason=reason,
                )
                analyze_groups(branch.children, effective)
                covered = (
                    TRUE
                    if branch.expression is None
                    else disjunction(covered, condition)
                )

    analyze_groups(tree.groups, TRUE)
    return tree


def analyze_source(source: str) -> ConditionalTree:
    """Parse and analyze conditional directives in source text."""

    return analyze_tree(parse_source(source))


def _expression_comparison_key(expression: Expression) -> tuple[object, ...]:
    """Return an order- and association-insensitive structural key."""

    if isinstance(expression, Constant):
        return ("constant", expression.value)
    if isinstance(expression, Variable):
        return ("variable", expression.name)
    if isinstance(expression, Predicate):
        return ("predicate", expression.text)
    if isinstance(expression, Negation):
        return ("not", _expression_comparison_key(expression.operand))

    operator = "and" if isinstance(expression, Conjunction) else "or"
    expression_type = type(expression)
    operands: list[Expression] = []

    def collect(item: Expression) -> None:
        if isinstance(item, expression_type):
            for operand in item.operands:
                collect(operand)
        else:
            operands.append(item)

    collect(expression)
    return (
        operator,
        tuple(sorted(_expression_comparison_key(item) for item in operands)),
    )


def _expressions_differ(
    left: Expression | None, right: Expression | None
) -> bool:
    if left is None or right is None:
        return left is not right
    return _expression_comparison_key(left) != _expression_comparison_key(right)


def _branch_differs_from_source(branch: ConditionalBranch) -> bool:
    """Return whether simplification or context changes the source condition."""

    assert branch.analysis is not None
    if branch.expression is None:
        return False
    return (
        _expressions_differ(branch.expression, branch.analysis.simplified)
        or _expressions_differ(branch.expression, branch.analysis.contextual)
    )


def _branch_is_notable(branch: ConditionalBranch) -> bool:
    assert branch.analysis is not None
    return (
        branch.analysis.status in {"dead", "redundant"}
        or _branch_differs_from_source(branch)
    )


@dataclass(frozen=True)
class _Visibility:
    branches: frozenset[int]
    groups: frozenset[int]
    detailed_branches: frozenset[int]


def _compute_visibility(tree: ConditionalTree, verbose: bool) -> _Visibility:
    """Compute visible branches and groups in one bottom-up tree walk."""

    visible_branches: set[int] = set()
    visible_groups: set[int] = set()
    detailed_branches: set[int] = set()

    def visit_group(group: ConditionalGroup) -> bool:
        group_visible = False
        for branch in group.branches:
            notable = _branch_is_notable(branch)
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
    return _Visibility(
        frozenset(visible_branches),
        frozenset(visible_groups),
        frozenset(detailed_branches),
    )


def _branch_dict(
    branch: ConditionalBranch, visibility: _Visibility
) -> dict[str, object]:
    assert branch.analysis is not None
    result: dict[str, object] = {
        "directive": branch.directive,
        "line": branch.line,
        "condition": branch.expression_text,
        "status": branch.analysis.status,
        "children": [
            _group_dict(group, visibility)
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
                "effective_condition": format_expression(
                    branch.analysis.effective
                ),
                "reason": branch.analysis.reason,
                "opaque_predicates": (
                    sorted(expression_predicates(branch.expression))
                    if branch.expression is not None
                    else []
                ),
            }
        )
    return result


def _group_dict(group: ConditionalGroup, visibility: _Visibility) -> dict[str, object]:
    return {
        "line": group.line,
        "end_line": group.end_line,
        "branches": [
            _branch_dict(branch, visibility)
            for branch in group.branches
            if id(branch) in visibility.branches
        ],
    }


def tree_to_dict(tree: ConditionalTree, *, verbose: bool = True) -> dict[str, object]:
    """Convert an analyzed tree to a JSON-serializable dictionary."""

    visibility = _compute_visibility(tree, verbose)
    return {
        "groups": [
            _group_dict(group, visibility)
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


def _colored(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS[color]}{text}{_COLOR_RESET}"


def _branch_color(branch: ConditionalBranch) -> str:
    assert branch.analysis is not None
    if branch.analysis.status == "dead":
        return "red"
    if branch.analysis.status == "redundant":
        return "yellow"
    if _branch_differs_from_source(branch):
        return "green"
    return "gray"


def _text_lines(
    groups: Sequence[ConditionalGroup],
    visibility: _Visibility,
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
            yield _colored(header, _branch_color(branch), color)
            if id(branch) not in visibility.detailed_branches:
                yield from _text_lines(
                    branch.children, visibility, depth + 1, color=color
                )
                continue
            if branch.analysis.reason:
                reason = f"{'  ' * (depth + 1)}reason: {branch.analysis.reason}"
                yield _colored(reason, _branch_color(branch), color)
            if branch.analysis.simplified is not None:
                simplified = format_expression(branch.analysis.simplified)
                contextual = format_expression(
                    branch.analysis.contextual or branch.analysis.simplified
                )
                simplified_line = f"{'  ' * (depth + 1)}simplified: {simplified}"
                simplified_color = (
                    "green"
                    if _expressions_differ(
                        branch.expression, branch.analysis.simplified
                    )
                    else "gray"
                )
                yield _colored(simplified_line, simplified_color, color)
                if contextual != simplified:
                    contextual_line = f"{'  ' * (depth + 1)}in context: {contextual}"
                    yield _colored(contextual_line, "green", color)
                predicates = sorted(expression_predicates(branch.expression))
                if predicates:
                    opaque = f"{'  ' * (depth + 1)}opaque: {', '.join(predicates)}"
                    yield _colored(opaque, "cyan", color)
            effective = (
                f"{'  ' * (depth + 1)}effective: "
                f"{format_expression(branch.analysis.effective)}"
            )
            yield _colored(effective, "gray", color)
            yield from _text_lines(
                branch.children, visibility, depth + 1, color=color
            )


def _render_report(
    tree: ConditionalTree, *, verbose: bool = True, color: bool = False
) -> tuple[str, bool]:
    """Render a report and indicate whether it contains branch entries."""

    visibility = _compute_visibility(tree, verbose)
    report = "\n".join(_text_lines(tree.groups, visibility, color=color))
    if report:
        return report, True
    if tree.groups:
        return "No changed, dead, or redundant conditional directives found.", False
    return "No conditional directives found.", False


def format_report(
    tree: ConditionalTree, *, verbose: bool = True, color: bool = False
) -> str:
    """Render a compact, indented representation of an analyzed tree."""

    return _render_report(tree, verbose=verbose, color=color)[0]


def _has_findings(tree: ConditionalTree) -> bool:
    for group in tree.groups:
        for branch in group.branches:
            assert branch.analysis is not None
            if branch.analysis.status in {"dead", "redundant"}:
                return True
            if _has_findings(ConditionalTree(branch.children)):
                return True
    return False


_SOURCE_SUFFIXES = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
)


def _source_paths(inputs: Sequence[Path], recursive: bool) -> list[Path]:
    """Expand files and, with ``recursive``, C/C++ source directories."""

    paths: list[Path] = []
    seen: set[Path] = set()
    for input_path in inputs:
        if input_path.is_file():
            candidates = [input_path]
        elif input_path.is_dir():
            if not recursive:
                raise ConditionError(
                    f"{input_path}: is a directory; use --recursive to scan it"
                )
            candidates = sorted(
                path
                for path in input_path.rglob("*")
                if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES
            )
            if not candidates:
                raise ConditionError(
                    f"{input_path}: no C/C++ source files found recursively"
                )
        else:
            raise ConditionError(f"{input_path}: no such file or directory")

        for path in candidates:
            identity = path.resolve()
            if identity not in seen:
                seen.add(identity)
                paths.append(path)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Boolean C/C++ preprocessor conditional directives."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="C/C++ source files, or directories used with --recursive",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively scan C/C++ source files under directory inputs",
    )
    parser.add_argument(
        "--json", action="store_true", help="write the conditional tree as JSON"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include unchanged conditional branches in the report",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit with status 1 when a dead or redundant branch is found",
    )
    args = parser.parse_args(argv)

    try:
        paths = _source_paths(args.sources, args.recursive)
    except ConditionError as error:
        parser.error(str(error))

    results: list[tuple[Path, ConditionalTree]] = []
    had_errors = False
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            results.append((path, analyze_source(source)))
        except (OSError, ConditionError, UnicodeDecodeError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            had_errors = True

    batch_mode = len(args.sources) > 1 or any(path.is_dir() for path in args.sources)
    if args.json:
        if batch_mode:
            files = []
            for path, tree in results:
                tree_dict = tree_to_dict(tree, verbose=args.verbose)
                if args.verbose or tree_dict["groups"]:
                    files.append({"path": str(path), **tree_dict})
            output = {"files": files}
        elif results:
            output = tree_to_dict(results[0][1], verbose=args.verbose)
        else:
            output = None
        if output is not None:
            print(json.dumps(output, indent=2))
    elif batch_mode:
        color = sys.stdout.isatty()
        reports = []
        for path, tree in results:
            report, has_entries = _render_report(
                tree, verbose=args.verbose, color=color
            )
            if args.verbose or has_entries:
                reports.append(
                    "\n".join(
                        (_colored(f"== {path} ==", "cyan", color), report)
                    )
                )
        if reports:
            print("\n\n".join(reports))
    elif results:
        print(
            format_report(
                results[0][1], verbose=args.verbose, color=sys.stdout.isatty()
            )
        )

    if had_errors:
        return 2
    has_findings = any(_has_findings(tree) for _, tree in results)
    return 1 if args.fail_on_findings and has_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
