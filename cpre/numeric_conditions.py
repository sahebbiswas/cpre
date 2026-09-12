"""Bounded concrete evaluation for integer preprocessor conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .expansion import Expansion, Token, tokenize
from .macros import MacroEnvironment
from .robdd import AnalysisBudget


class NumericConditionError(ValueError):
    """A concrete condition uses syntax we intentionally cannot evaluate."""


_INTEGER = re.compile(
    r"(?P<body>0[xX][0-9a-fA-F']+|0[bB][01']+|0[0-7']*|[1-9][0-9']*|0)"
    r"(?P<suffix>(?:[uU](?:ll|LL|[lL])?|(?:ll|LL|[lL])[uU]?|[zZ][uU]?|[uU][zZ]?))?\Z"
)
_INT_BITS = 64
_UINT_MASK = (1 << _INT_BITS) - 1
_SIGNED_MIN = -(1 << (_INT_BITS - 1))
_SIGNED_MAX = (1 << (_INT_BITS - 1)) - 1
_MAX_SHIFT = _INT_BITS - 1
# A parenthesized subexpression re-enters the full precedence stack, so keep
# this comfortably below Python's own recursion limit rather than relying on it.
_MAX_DEPTH = 48


@dataclass(frozen=True)
class _Value:
    value: int
    unsigned: bool = False

    def truth(self) -> bool:
        return self.value != 0


def _signed(value: int) -> _Value:
    if value < _SIGNED_MIN or value > _SIGNED_MAX:
        raise NumericConditionError("signed integer expression exceeds concrete-evaluation range")
    return _Value(value)


def _unsigned(value: int) -> _Value:
    return _Value(value & _UINT_MASK, True)


def _convert(left: _Value, right: _Value) -> tuple[_Value, _Value, bool]:
    """Apply #if's intmax_t/uintmax_t usual arithmetic conversion."""
    if left.unsigned or right.unsigned:
        return _unsigned(left.value), _unsigned(right.value), True
    return left, right, False


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


def _integer(text: str) -> _Value:
    match = _INTEGER.fullmatch(text)
    if match is None:
        raise NumericConditionError(f"unsupported integer constant {text!r}")
    body = match.group("body").replace("'", "")
    suffix = (match.group("suffix") or "").lower()
    base = (16 if body.lower().startswith("0x") else
            2 if body.lower().startswith("0b") else
            8 if len(body) > 1 and body.startswith("0") else 10)
    value = int(body, base)
    if value > _UINT_MASK:
        raise NumericConditionError("integer constant exceeds concrete-evaluation range")
    if "u" in suffix:
        return _unsigned(value)
    if value <= _SIGNED_MAX:
        return _signed(value)
    # For non-decimal constants the preprocessor may select uintmax_t when the
    # value is not representable by intmax_t. Decimal overflow is kept explicit.
    if base != 10:
        return _unsigned(value)
    raise NumericConditionError("decimal integer constant exceeds signed concrete-evaluation range")


def _divide(left: int, right: int) -> int:
    if right == 0:
        raise NumericConditionError("division by zero in concrete condition")
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


class _Parser:
    def __init__(self, tokens: list[Token], budget: AnalysisBudget) -> None:
        self.tokens, self.budget, self.index, self.depth = tokens, budget, 0, 0

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

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise NumericConditionError("expression nesting exceeds concrete-evaluation bound")

    def _leave(self) -> None:
        self.depth -= 1

    def parse(self) -> _Value:
        value = self.logical_or(True)
        if self.peek() is not None:
            raise NumericConditionError(f"unsupported token {self.peek().text!r} in concrete condition")
        return value

    def primary(self, evaluate: bool) -> _Value:
        if self.take("(") is not None:
            self._enter()
            try:
                value = self.logical_or(evaluate)
            finally:
                self._leave()
            if self.take(")") is None:
                raise NumericConditionError("expected ')' in concrete condition")
            return value
        token = self.take()
        if token is None:
            raise NumericConditionError("expected operand in concrete condition")
        if token.kind == "number":
            return _integer(token.text) if evaluate else _Value(0)
        if token.kind == "identifier":
            raise NumericConditionError(f"unresolved identifier {token.text!r}")
        raise NumericConditionError(f"unsupported token {token.text!r} in concrete condition")

    def unary(self, evaluate: bool) -> _Value:
        if self.peek() is not None and self.peek().text in {"+", "-", "!", "~"}:
            op = self.take().text
            self._enter()
            try:
                value = self.unary(evaluate)
            finally:
                self._leave()
            if not evaluate:
                return _Value(0)
            if op == "+":
                return value
            if op == "!":
                return _Value(int(not value.truth()))
            if op == "-":
                return _unsigned(-value.value) if value.unsigned else _signed(-value.value)
            return _unsigned(~value.value) if value.unsigned else _signed(~value.value)
        return self.primary(evaluate)

    def binary(self, operand, operators: set[str], evaluate: bool, apply) -> _Value:
        value = operand(evaluate)
        while self.peek() is not None and self.peek().text in operators:
            op = self.take().text
            right = operand(evaluate)
            if evaluate:
                value = apply(op, value, right)
        return value

    def multiplicative(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value) -> _Value:
            left, right, unsigned = _convert(left, right)
            if op == "*":
                result = left.value * right.value
            elif unsigned:
                if right.value == 0:
                    raise NumericConditionError("division by zero in concrete condition")
                result = left.value // right.value if op == "/" else left.value % right.value
            else:
                quotient = _divide(left.value, right.value)
                result = quotient if op == "/" else left.value - quotient * right.value
            return _unsigned(result) if unsigned else _signed(result)
        return self.binary(self.unary, {"*", "/", "%"}, evaluate, apply)

    def additive(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value) -> _Value:
            left, right, unsigned = _convert(left, right)
            result = left.value + right.value if op == "+" else left.value - right.value
            return _unsigned(result) if unsigned else _signed(result)
        return self.binary(self.multiplicative, {"+", "-"}, evaluate, apply)

    def shift(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value) -> _Value:
            if right.value < 0 or right.value > _MAX_SHIFT:
                raise NumericConditionError("shift count exceeds concrete-evaluation bound")
            if op == "<<":
                result = left.value << right.value
                return _unsigned(result) if left.unsigned else _signed(result)
            return _unsigned(left.value >> right.value) if left.unsigned else _signed(left.value >> right.value)
        return self.binary(self.additive, {"<<", ">>"}, evaluate, apply)

    def relational(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value) -> _Value:
            left, right, _ = _convert(left, right)
            return _Value(int({"<": left.value < right.value, "<=": left.value <= right.value,
                               ">": left.value > right.value, ">=": left.value >= right.value}[op]))
        return self.binary(self.shift, {"<", "<=", ">", ">="}, evaluate, apply)

    def equality(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value) -> _Value:
            left, right, _ = _convert(left, right)
            return _Value(int((left.value == right.value) if op == "==" else (left.value != right.value)))
        return self.binary(self.relational, {"==", "!="}, evaluate, apply)

    def bitand(self, evaluate: bool) -> _Value:
        return self.binary(self.equality, {"&"}, evaluate, self._bitwise)

    def bitxor(self, evaluate: bool) -> _Value:
        return self.binary(self.bitand, {"^"}, evaluate, self._bitwise)

    def bitor(self, evaluate: bool) -> _Value:
        return self.binary(self.bitxor, {"|"}, evaluate, self._bitwise)

    @staticmethod
    def _bitwise(op: str, left: _Value, right: _Value) -> _Value:
        left, right, unsigned = _convert(left, right)
        result = {"&": left.value & right.value,
                  "^": left.value ^ right.value,
                  "|": left.value | right.value}[op]
        return _unsigned(result) if unsigned else _signed(result)

    def logical_and(self, evaluate: bool) -> _Value:
        value = self.bitor(evaluate)
        while self.take("&&") is not None:
            right = self.bitor(evaluate and value.truth())
            if evaluate:
                value = _Value(int(value.truth() and right.truth()))
        return value

    def logical_or(self, evaluate: bool) -> _Value:
        value = self.logical_and(evaluate)
        while self.take("||") is not None:
            right = self.logical_and(evaluate and not value.truth())
            if evaluate:
                value = _Value(int(value.truth() or right.truth()))
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
        return _Parser(significant, budget).parse().truth()
    except NumericConditionError as error:
        if str(error).startswith("unresolved identifier"):
            return None
        raise


__all__ = ["NumericConditionError", "evaluate_numeric_condition"]
