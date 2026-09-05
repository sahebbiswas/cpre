"""Internal Boolean expression parsing and algebra."""

from __future__ import annotations

import re
from typing import Iterator, Sequence

from .model import (
    BooleanAtom,
    Conjunction,
    Constant,
    DefinedVariable,
    Disjunction,
    Expression,
    ExpressionSyntaxError,
    FALSE,
    Negation,
    Predicate,
    SourceLocation,
    TRUE,
    Variable,
)

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


class Token:
    __slots__ = ("kind", "text", "location")

    def __init__(self, kind: str, text: str, location: SourceLocation) -> None:
        self.kind = kind
        self.text = text
        self.location = location


def tokens(text: str, locations: Sequence[SourceLocation] | None = None) -> list[Token]:
    if locations is not None and len(locations) != len(text):
        raise ValueError("source locations must correspond to every input character")

    def location_at(offset: int) -> SourceLocation:
        if locations is None:
            return SourceLocation(None, offset + 1)
        return locations[offset]

    result: list[Token] = []
    offset = 0
    while offset < len(text):
        match = _TOKEN_RE.match(text, offset)
        if not match:
            if text[offset:].strip():
                raise ExpressionSyntaxError(
                    f"unsupported input: {text[offset:]!r}", location=location_at(offset)
                )
            break
        kind = match.lastgroup
        assert kind is not None
        token_text = match.group(kind)
        result.append(Token(kind, token_text, location_at(match.start(kind))))
        offset = match.end()
    return result


class ExpressionParser:
    def __init__(self, text: str, locations: Sequence[SourceLocation] | None = None) -> None:
        self.text = text
        self.tokens = tokens(text, locations)

    def parse(self) -> Expression:
        if not self.tokens:
            raise ExpressionSyntaxError("expected a Boolean expression")
        return self._parse_tokens(self.tokens)

    def _parse_tokens(self, items: Sequence[Token]) -> Expression:
        if not items:
            raise ExpressionSyntaxError("expected an operand")
        self._validate_parentheses(items)
        if self._has_top_level_other(items, {"?", ","}):
            return Predicate(self._normalize_predicate(items))
        parts = self._split_top_level(items, "or")
        if len(parts) > 1:
            return Disjunction(tuple(self._parse_tokens(part) for part in parts))
        parts = self._split_top_level(items, "and")
        if len(parts) > 1:
            return Conjunction(tuple(self._parse_tokens(part) for part in parts))
        if self._is_wrapped(items):
            return self._parse_tokens(items[1:-1])
        if items[0].kind == "not":
            operand_items = items[1:]
            operand = self._parse_tokens(operand_items)
            if isinstance(operand, Predicate) and not self._is_wrapped(operand_items):
                return Predicate(self._normalize_predicate(items))
            return Negation(operand)
        defined = self._parse_defined(items)
        if defined is not None:
            return defined
        if len(items) == 1 and items[0].kind == "identifier":
            return Variable(items[0].text)
        if len(items) == 1 and items[0].kind == "number":
            return self._parse_number(items[0])
        return Predicate(self._normalize_predicate(items))

    @staticmethod
    def _validate_parentheses(items: Sequence[Token]) -> None:
        openings: list[Token] = []
        for token in items:
            if token.kind == "lparen":
                openings.append(token)
            elif token.kind == "rparen":
                if not openings:
                    raise ExpressionSyntaxError("unexpected ')'", location=token.location)
                openings.pop()
        if openings:
            opening = openings[-1]
            raise ExpressionSyntaxError(
                "expected ')' before end of expression; unmatched '('",
                location=opening.location,
            )

    @staticmethod
    def _is_wrapped(items: Sequence[Token]) -> bool:
        if len(items) < 2 or items[0].kind != "lparen":
            return False
        depth = 0
        for index, token in enumerate(items):
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
                if depth == 0:
                    return index == len(items) - 1
        return False

    @staticmethod
    def _split_top_level(items: Sequence[Token], operator: str) -> list[Sequence[Token]]:
        depth = 0
        start = 0
        parts: list[Sequence[Token]] = []
        for index, token in enumerate(items):
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
            elif depth == 0 and token.kind == operator:
                if index == start:
                    raise ExpressionSyntaxError("expected an operand", location=token.location)
                parts.append(items[start:index])
                start = index + 1
        if parts:
            if start == len(items):
                token = items[-1]
                raise ExpressionSyntaxError(
                    f"expected an operand after {token.text!r}", location=token.location
                )
            parts.append(items[start:])
        return parts or [items]

    @staticmethod
    def _has_top_level_other(items: Sequence[Token], operators: set[str]) -> bool:
        depth = 0
        for token in items:
            if token.kind == "lparen":
                depth += 1
            elif token.kind == "rparen":
                depth -= 1
            elif depth == 0 and token.kind == "other":
                if any(operator in token.text for operator in operators):
                    return True
        return False

    @staticmethod
    def _parse_defined(items: Sequence[Token]) -> Expression | None:
        if not items or items[0].kind != "identifier" or items[0].text != "defined":
            return None
        if len(items) == 2 and items[1].kind == "identifier":
            return DefinedVariable(items[1].text)
        if (
            len(items) == 4
            and items[1].kind == "lparen"
            and items[2].kind == "identifier"
            and items[3].kind == "rparen"
        ):
            return DefinedVariable(items[2].text)
        return None

    @staticmethod
    def _parse_number(token: Token) -> Constant:
        digits = re.sub(r"[uUlL]+$", "", token.text)
        base = 16 if digits.lower().startswith("0x") else (8 if len(digits) > 1 and digits.startswith("0") else 10)
        try:
            return Constant(int(digits, base) != 0)
        except ValueError as error:
            raise ExpressionSyntaxError(
                f"invalid integer {token.text!r}", location=token.location
            ) from error

    @staticmethod
    def _normalize_predicate(items: Sequence[Token]) -> str:
        parts: list[str] = []
        previous: Token | None = None
        for token in items:
            needs_space = previous is not None
            if token.kind == "rparen" or (previous is not None and previous.kind == "lparen"):
                needs_space = False
            if token.kind == "lparen" and previous is not None and previous.kind == "identifier":
                needs_space = False
            if needs_space:
                parts.append(" ")
            parts.append(token.text)
            previous = token
        return "".join(parts)


def parse_expression(text: str) -> Expression:
    return ExpressionParser(text).parse()


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
        operands.extend(expression.operands if isinstance(expression, Conjunction) else (expression,))
    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return FALSE
    filtered = [
        operand for operand in unique
        if not (isinstance(operand, Disjunction) and any(term in unique for term in operand.operands))
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
        operands.extend(expression.operands if isinstance(expression, Disjunction) else (expression,))
    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return TRUE
    filtered = [
        operand for operand in unique
        if not (isinstance(operand, Conjunction) and any(term in unique for term in operand.operands))
    ]
    if not filtered:
        return FALSE
    if len(filtered) == 1:
        return filtered[0]
    return Disjunction(tuple(sorted(filtered, key=_sort_key)))


def simplify(expression: Expression) -> Expression:
    if isinstance(expression, (Constant, Variable, Predicate)):
        return expression
    if isinstance(expression, Negation):
        return negate(expression.operand)
    if isinstance(expression, Conjunction):
        return conjunction(*expression.operands)
    return disjunction(*expression.operands)


def _precedence(expression: Expression) -> int:
    if isinstance(expression, Predicate):
        return 0
    if isinstance(expression, Disjunction):
        return 1
    if isinstance(expression, Conjunction):
        return 2
    if isinstance(expression, Negation):
        return 3
    return 4


def format_expression(expression: Expression, parent_precedence: int = 0) -> str:
    if isinstance(expression, Constant):
        text = "1" if expression.value else "0"
    elif isinstance(expression, DefinedVariable):
        text = f"defined({expression.name})"
    elif isinstance(expression, Variable):
        text = expression.name
    elif isinstance(expression, Predicate):
        text = expression.text
    elif isinstance(expression, Negation):
        text = f"!{format_expression(expression.operand, _precedence(expression))}"
    else:
        operator = " && " if isinstance(expression, Conjunction) else " || "
        precedence = _precedence(expression)
        text = operator.join(format_expression(item, precedence) for item in expression.operands)
    return f"({text})" if _precedence(expression) < parent_precedence else text


def expression_atoms(expression: Expression) -> set[BooleanAtom]:
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


def expression_atoms_in_order(expression: Expression) -> Iterator[BooleanAtom]:
    if isinstance(expression, (Variable, Predicate)):
        yield expression
    elif isinstance(expression, Negation):
        yield from expression_atoms_in_order(expression.operand)
    elif isinstance(expression, (Conjunction, Disjunction)):
        for operand in expression.operands:
            yield from expression_atoms_in_order(operand)


def expression_predicates(expression: Expression) -> set[str]:
    return {atom.text for atom in expression_atoms(expression) if isinstance(atom, Predicate)}


def expression_comparison_key(expression: Expression) -> tuple[object, ...]:
    if isinstance(expression, Constant):
        return ("constant", expression.value)
    if isinstance(expression, DefinedVariable):
        return ("defined", expression.name)
    if isinstance(expression, Variable):
        return ("variable", expression.name)
    if isinstance(expression, Predicate):
        return ("predicate", expression.text)
    if isinstance(expression, Negation):
        return ("not", expression_comparison_key(expression.operand))
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
    return (operator, tuple(sorted(expression_comparison_key(item) for item in operands)))


def expressions_differ(left: Expression | None, right: Expression | None) -> bool:
    if left is None or right is None:
        return left is not right
    return expression_comparison_key(left) != expression_comparison_key(right)


__all__ = [
    "ExpressionParser", "conjunction", "disjunction", "expression_atoms",
    "expression_atoms_in_order", "expression_comparison_key", "expression_predicates",
    "expressions_differ", "format_expression", "negate", "parse_expression", "simplify", "tokens"
]
