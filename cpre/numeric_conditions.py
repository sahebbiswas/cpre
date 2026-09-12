"""Bounded concrete evaluation for integer preprocessor conditions."""

from __future__ import annotations

import re

from .expansion import Expansion, Token, tokenize
from .macros import MacroEnvironment
from .robdd import AnalysisBudget


class NumericConditionError(ValueError):
    """A concrete condition uses syntax we intentionally cannot evaluate."""


_INTEGER = re.compile(
    r"(?P<body>0[xX][0-9a-fA-F']+|0[bB][01']+|0[0-7']*|[1-9][0-9']*|0)"
    r"(?P<suffix>(?:[uU](?:ll|LL|[lL])?|(?:ll|LL|[lL])[uU]?|[zZ][uU]?|[uU][zZ]?))?\Z"
)
_MAX_BITS = 4096
_MAX_SHIFT = 4096


def _resolve_defined(tokens: list[Token], environment: MacroEnvironment) -> list[Token] | None:
    result: list[Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "identifier" or token.text != "defined":
            result.append(token)
            index += 1
            continue
        if index + 1 < len(tokens) and tokens[index + 1].kind == "identifier":
            name, end, index = tokens[index + 1].text, tokens[index + 1].end, index + 2
        elif (index + 3 < len(tokens) and tokens[index + 1].text == "("
              and tokens[index + 2].kind == "identifier" and tokens[index + 3].text == ")"):
            name, end, index = tokens[index + 2].text, tokens[index + 3].end, index + 4
        else:
            raise NumericConditionError("malformed defined operator")
        defined = environment.get(name).defined
        if defined is None:
            return None
        result.append(Token("1" if defined else "0", "number", token.start, end, generated=True))
    return result


def _integer(text: str) -> int:
    match = _INTEGER.fullmatch(text)
    if match is None:
        raise NumericConditionError(f"unsupported integer constant {text!r}")
    body = match.group("body").replace("'", "")
    base = (16 if body.lower().startswith("0x") else
            2 if body.lower().startswith("0b") else
            8 if len(body) > 1 and body.startswith("0") else 10)
    value = int(body, base)
    if value.bit_length() > _MAX_BITS:
        raise NumericConditionError("integer constant exceeds concrete-evaluation bound")
    return value


def _bounded(value: int) -> int:
    if value.bit_length() > _MAX_BITS:
        raise NumericConditionError("integer expression exceeds concrete-evaluation bound")
    return value


def _divide(left: int, right: int) -> int:
    if right == 0:
        raise NumericConditionError("division by zero in concrete condition")
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


class _Parser:
    def __init__(self, tokens: list[Token], budget: AnalysisBudget) -> None:
        self.tokens, self.budget, self.index = tokens, budget, 0

    def peek(self, text: str | None = None) -> Token | None:
        if self.index >= len(self.tokens):
            return None
        token = self.tokens[self.index]
        return token if text is None or token.text == text else None

    def take(self, text: str | None = None) -> Token | None:
        token = self.peek(text)
        if token is not None:
            self.index += 1
            self.budget.consume()
        return token

    def parse(self) -> int:
        value = self.logical_or(True)
        if self.peek() is not None:
            raise NumericConditionError(f"unsupported token {self.peek().text!r} in concrete condition")
        return value

    def primary(self, evaluate: bool) -> int:
        if self.take("(") is not None:
            value = self.logical_or(evaluate)
            if self.take(")") is None:
                raise NumericConditionError("expected ')' in concrete condition")
            return value
        token = self.take()
        if token is None:
            raise NumericConditionError("expected operand in concrete condition")
        if token.kind == "number":
            return _integer(token.text) if evaluate else 0
        if token.kind == "identifier":
            raise NumericConditionError(f"unresolved identifier {token.text!r}")
        raise NumericConditionError(f"unsupported token {token.text!r} in concrete condition")

    def unary(self, evaluate: bool) -> int:
        if self.peek() is not None and self.peek().text in {"+", "-", "!", "~"}:
            op = self.take().text
            value = self.unary(evaluate)
            if not evaluate:
                return 0
            return {"+": lambda: value, "-": lambda: _bounded(-value),
                    "!": lambda: int(not value), "~": lambda: _bounded(~value)}[op]()
        return self.primary(evaluate)

    def binary(self, operand, operators: set[str], evaluate: bool, apply) -> int:
        value = operand(evaluate)
        while self.peek() is not None and self.peek().text in operators:
            op = self.take().text
            right = operand(evaluate)
            if evaluate:
                value = apply(op, value, right)
        return value

    def multiplicative(self, evaluate: bool) -> int:
        def apply(op, left, right):
            if op == "*": return _bounded(left * right)
            quotient = _divide(left, right)
            return _bounded(quotient if op == "/" else left - quotient * right)
        return self.binary(self.unary, {"*", "/", "%"}, evaluate, apply)

    def additive(self, evaluate: bool) -> int:
        return self.binary(self.multiplicative, {"+", "-"}, evaluate,
                           lambda op, l, r: _bounded(l + r if op == "+" else l - r))

    def shift(self, evaluate: bool) -> int:
        def apply(op, left, right):
            if right < 0 or right > _MAX_SHIFT:
                raise NumericConditionError("shift count exceeds concrete-evaluation bound")
            return _bounded(left << right) if op == "<<" else left >> right
        return self.binary(self.additive, {"<<", ">>"}, evaluate, apply)

    def relational(self, evaluate: bool) -> int:
        def apply(op, left, right):
            return int({"<": left < right, "<=": left <= right,
                        ">": left > right, ">=": left >= right}[op])
        return self.binary(self.shift, {"<", "<=", ">", ">="}, evaluate, apply)

    def equality(self, evaluate: bool) -> int:
        return self.binary(self.relational, {"==", "!="}, evaluate,
                           lambda op, l, r: int((l == r) if op == "==" else (l != r)))

    def bitand(self, evaluate: bool) -> int:
        return self.binary(self.equality, {"&"}, evaluate, lambda _o, l, r: _bounded(l & r))

    def bitxor(self, evaluate: bool) -> int:
        return self.binary(self.bitand, {"^"}, evaluate, lambda _o, l, r: _bounded(l ^ r))

    def bitor(self, evaluate: bool) -> int:
        return self.binary(self.bitxor, {"|"}, evaluate, lambda _o, l, r: _bounded(l | r))

    def logical_and(self, evaluate: bool) -> int:
        value = self.bitor(evaluate)
        while self.take("&&") is not None:
            right = self.bitor(evaluate and bool(value))
            if evaluate: value = int(bool(value) and bool(right))
        return value

    def logical_or(self, evaluate: bool) -> int:
        value = self.logical_and(evaluate)
        while self.take("||") is not None:
            right = self.logical_and(evaluate and not bool(value))
            if evaluate: value = int(bool(value) or bool(right))
        return value


def evaluate_numeric_condition(text: str, environment: MacroEnvironment,
                               expansion: Expansion, budget: AnalysisBudget) -> bool | None:
    """Evaluate a macro-expanded integer condition, or return None if still unknown."""
    original = [t for t in tokenize(text) if t.kind not in {"space", "comment"}]
    resolved = _resolve_defined(original, environment)
    if resolved is None:
        return None
    expanded = expansion._expand(resolved, environment)
    significant = [token for token in expanded if token.kind != "empty"]
    try:
        return bool(_Parser(significant, budget).parse())
    except NumericConditionError as error:
        if str(error).startswith("unresolved identifier"):
            return None
        raise


__all__ = ["NumericConditionError", "evaluate_numeric_condition"]
