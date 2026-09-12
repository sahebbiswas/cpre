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
_MAX_INTEGER_BITS = 4096
_MAX_SHIFT = 4096


def _known_defined(environment: MacroEnvironment, name: str) -> bool | None:
    return environment.get(name).defined


def _resolve_defined(tokens: list[Token], environment: MacroEnvironment) -> list[Token] | None:
    """Replace defined operands before ordinary macro expansion, per C semantics."""
    result: list[Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "identifier" or token.text != "defined":
            result.append(token)
            index += 1
            continue

        name: str | None = None
        end = token.end
        if index + 1 < len(tokens) and tokens[index + 1].kind == "identifier":
            name = tokens[index + 1].text
            end = tokens[index + 1].end
            index += 2
        elif (
            index + 3 < len(tokens)
            and tokens[index + 1].text == "("
            and tokens[index + 2].kind == "identifier"
            and tokens[index + 3].text == ")"
        ):
            name = tokens[index + 2].text
            end = tokens[index + 3].end
            index += 4
        else:
            raise NumericConditionError("malformed defined operator")

        defined = _known_defined(environment, name)
        if defined is None:
            return None
        result.append(Token("1" if defined else "0", "number", token.start, end, generated=True))
    return result


def _integer(text: str) -> int:
    match = _INTEGER.fullmatch(text)
    if match is None:
        raise NumericConditionError(f"unsupported integer constant {text!r}")
    body = match.group("body").replace("'", "")
    if body.lower().startswith("0x"):
        base = 16
    elif body.lower().startswith("0b"):
        base = 2
    elif len(body) > 1 and body.startswith("0"):
        base = 8
    else:
        base = 10
    value = int(body, base)
    if value.bit_length() > _MAX_INTEGER_BITS:
        raise NumericConditionError("integer constant exceeds concrete-evaluation bound")
    return value


def _bounded(value: int) -> int:
    if value.bit_length() > _MAX_INTEGER_BITS:
        raise NumericConditionError("integer expression exceeds concrete-evaluation bound")
    return value


def _c_div(left: int, right: int) -> int:
    if right == 0:
        raise NumericConditionError("division by zero in concrete condition")
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


class _Parser:
    def __init__(self, tokens: list[Token], budget: AnalysisBudget) -> None:
        self.tokens = tokens
        self.budget = budget
        self.index = 0

    def _peek(self, text: str | None = None) -> Token | None:
        if self.index >= len(self.tokens):
            return None
        token = self.tokens[self.index]
        return token if text is None or token.text == text else None

    def _take(self, text: str | None = None) -> Token | None:
        token = self._peek(text)
        if token is not None:
            self.index += 1
            self.budget.consume()
        return token

    def _expect(self, text: str) -> None:
        if self._take(text) is None:
            raise NumericConditionError(f"expected {text!r} in concrete condition")

    def parse(self) -> int:
        if not self.tokens:
            raise NumericConditionError("empty concrete condition")
        value = self._logical_or(True)
        if self._peek() is not None:
            raise NumericConditionError(
                f"unsupported token {self._peek().text!r} in concrete condition"
            )
        return value

    def _primary(self, evaluate: bool) -> int:
        token = self._peek()
        if token is None:
            raise NumericConditionError("expected operand in concrete condition")
        if self._take("(") is not None:
            value = self._logical_or(evaluate)
            self._expect(")")
            return value
        token = self._take()
        assert token is not None
        if token.kind == "number":
            return _integer(token.text) if evaluate else 0
        if token.kind == "identifier":
            # Unlike a full preprocessor, cpre deliberately keeps unconfigured
            # identifiers unknown rather than silently treating them as zero.
            raise NumericConditionError(f"unresolved identifier {token.text!r}")
        raise NumericConditionError(f"unsupported token {token.text!r} in concrete condition")

    def _unary(self, evaluate: bool) -> int:
        token = self._peek()
        if token is not None and token.text in {"+", "-", "!", "~"}:
            op = self._take().text
            value = self._unary(evaluate)
            if not evaluate:
                return 0
            if op == "+":
                return value
            if op == "-":
                return _bounded(-value)
            if op == "!":
                return int(not value)
            return _bounded(~value)
        return self._primary(evaluate)

    def _binary(self, operand, operators: set[str], evaluate: bool, apply) -> int:
        value = operand(evaluate)
        while self._peek() is not None and self._peek().text in operators:
            op = self._take().text
            right = operand(evaluate)
            if evaluate:
                value = apply(op, value, right)
        return value

    def _multiplicative(self, evaluate: bool) -> int:
        def apply(op: str, left: int, right: int) -> int:
            if op == "*":
                return _bounded(left * right)
            if op == "/":
                return _bounded(_c_div(left, right))
            quotient = _c_div(left, right)
            return _bounded(left - quotient * right)
        return self._binary(self._unary, {"*", "/", "%"}, evaluate, apply)

    def _additive(self, evaluate: bool) -> int:
        return self._binary(
            self._multiplicative,
            {"+", "-"},
            evaluate,
            lambda op, left, right: _bounded(left + right if op == "+" else left - right),
        )

    def _shift(self, evaluate: bool) -> int:
        def apply(op: str, left: int, right: int) -> int:
            if right < 0 or right > _MAX_SHIFT:
                raise NumericConditionError("shift count exceeds concrete-evaluation bound")
            if op == "<<":
                return _bounded(left << right)
            return left >> right
        return self._binary(self._additive, {"<<", ">>"}, evaluate, apply)

    def _relational(self, evaluate: bool) -> int:
        return self._binary(
            self._shift,
            {"<", "<=", ">", ">="},
            evaluate,
            lambda op, left, right: int({
                "<": left < right,
                "<=": left <= right,
                ">": left > right,
                ">=": left >= right,
            }[op]),
        )

    def _equality(self, evaluate: bool) -> int:
        return self._binary(
            self._relational,
            {"==", "!="},
            evaluate,
            lambda op, left, right: int((left == right) if op == "==" else (left != right)),
        )

    def _bitand(self, evaluate: bool) -> int:
        return self._binary(
            self._equality, {"&"}, evaluate,
            lambda _op, left, right: _bounded(left & right),
        )

    def _bitxor(self, evaluate: bool) -> int:
        return self._binary(
            self._bitand, {"^"}, evaluate,
            lambda _op, left, right: _bounded(left ^ right),
        )

    def _bitor(self, evaluate: bool) -> int:
        return self._binary(
            self._bitxor, {"|"}, evaluate,
            lambda _op, left, right: _bounded(left | right),
        )

    def _logical_and(self, evaluate: bool) -> int:
        value = self._bitor(evaluate)
        while self._take("&&") is not None:
            evaluate_right = evaluate and bool(value)
            right = self._bitor(evaluate_right)
            if evaluate:
                value = int(bool(value) and bool(right))
        return value

    def _logical_or(self, evaluate: bool) -> int:
        value = self._logical_and(evaluate)
        while self._take("||") is not None:
            evaluate_right = evaluate and not bool(value)
            right = self._logical_and(evaluate_right)
            if evaluate:
                value = int(bool(value) or bool(right))
        return value


def evaluate_numeric_condition(
    text: str,
    environment: MacroEnvironment,
    expansion: Expansion,
    budget: AnalysisBudget,
) -> bool | None:
    """Evaluate one macro-expanded integer condition, or return None if unknown."""
    original = [token for token in tokenize(text) if token.kind not in {"space", "comment"}]
    resolved = _resolve_defined(original, environment)
    if resolved is None:
        return None
    expanded = expansion.expand_tokens(resolved, environment)
    significant = [token for token in expanded if token.kind != "empty"]
    try:
        return bool(_Parser(significant, budget).parse())
    except NumericConditionError as error:
        if str(error).startswith("unresolved identifier"):
            return None
        raise


__all__ = ["NumericConditionError", "evaluate_numeric_condition"]
