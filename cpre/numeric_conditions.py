"""Bounded concrete evaluation for integer preprocessor conditions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from .expansion import Expansion, ExpansionError
from .lexer import Token, tokenize
from .macros import MacroLookup
from .robdd import AnalysisBudget


class FailureKind(str, Enum):
    """Why an integer expression has no concrete value."""

    UNRESOLVED_IDENTIFIER = "unresolved-identifier"
    DIVISION_BY_ZERO = "division-by-zero"
    OVERFLOW = "overflow"
    IMPLEMENTATION_DEFINED = "implementation-defined"
    NESTING_BOUND = "nesting-bound"
    UNSUPPORTED = "unsupported"


class NumericConditionError(ValueError):
    """A concrete condition uses syntax we intentionally cannot evaluate.

    ``offset`` is the offset in the condition text of the token the error concerns,
    when one is known, so a caller can report its physical position. ``kind`` says
    why evaluation stopped; anything not otherwise classified is ``UNSUPPORTED``.
    """

    def __init__(
        self,
        message: str,
        offset: int | None = None,
        kind: FailureKind = FailureKind.UNSUPPORTED,
    ) -> None:
        super().__init__(message)
        self.offset = offset
        self.kind = kind


_INTEGER = re.compile(
    r"(?P<body>0[xX][0-9a-fA-F']+|0[bB][01']+|0[0-7']*|[1-9][0-9']*|0)"
    r"(?P<suffix>(?:[uU](?:ll|LL|[lL])?|(?:ll|LL|[lL])[uU]?|[zZ][uU]?|[uU][zZ]?))?\Z"
)
_CHARACTER = re.compile(r"(?P<prefix>u8|u|U|L)?'(?P<body>(?:\\.|[^'\\])*)'\Z", re.DOTALL)
_INT_BITS = 64
_UINT_MASK = (1 << _INT_BITS) - 1
_SIGNED_MIN = -(1 << (_INT_BITS - 1))
_SIGNED_MAX = (1 << (_INT_BITS - 1)) - 1
_MAX_SHIFT = _INT_BITS - 1
# A parenthesized/conditional subexpression re-enters the full precedence stack.
# Keep this well below Python's recursion limit so our structured bound fires first.
_MAX_DEPTH = 24
_BASIC_RAW_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_{}[]#()<>%:;.?*+-/^&|~!=, "
)
_SIMPLE_ESCAPES = {
    "'": "'",
    '"': '"',
    "?": "?",
    "\\": "\\",
    "a": "escape:a",
    "b": "escape:b",
    "f": "escape:f",
    "n": "escape:n",
    "r": "escape:r",
    "t": "escape:t",
    "v": "escape:v",
}


@dataclass(frozen=True)
class _Value:
    value: int | None
    unsigned: bool = False
    truth_value: bool | None = None
    character: str | None = None
    reason: str | None = None
    # Where an unknown value came from, so a result can point at the constant itself.
    origin: int | None = None

    def truth(self) -> bool:
        if self.value is not None:
            return self.value != 0
        if self.truth_value is not None:
            return self.truth_value
        raise NumericConditionError(
            self.reason or "expression value is implementation-defined in concrete evaluation",
            self.origin,
            FailureKind.IMPLEMENTATION_DEFINED,
        )


def _signed(value: int) -> _Value:
    if value < _SIGNED_MIN or value > _SIGNED_MAX:
        raise NumericConditionError(
            "signed integer expression exceeds concrete-evaluation range",
            kind=FailureKind.OVERFLOW,
        )
    return _Value(value)


def _unsigned(value: int) -> _Value:
    return _Value(value & _UINT_MASK, True)


def _as_unsigned(value: _Value) -> _Value:
    if value.value is not None:
        return _unsigned(value.value)
    return _Value(
        None,
        True,
        value.truth_value,
        value.character,
        value.reason,
    )


def _convert(left: _Value, right: _Value) -> tuple[_Value, _Value, bool]:
    """Apply #if's intmax_t/uintmax_t usual arithmetic conversion."""
    if left.unsigned or right.unsigned:
        return _as_unsigned(left), _as_unsigned(right), True
    return left, right, False


def _numeric(value: _Value) -> int:
    if value.value is not None:
        return value.value
    raise NumericConditionError(
        value.reason or "expression depends on an implementation-defined character constant value",
        value.origin,
        FailureKind.IMPLEMENTATION_DEFINED,
    )


def _unknown_origin(*values: _Value) -> int | None:
    for value in values:
        if value.reason:
            return value.origin
    return None


def _unknown_reason(*values: _Value) -> str:
    for value in values:
        if value.reason:
            return value.reason
    return "expression depends on an implementation-defined character constant value"


@dataclass(frozen=True)
class _Unresolved:
    """The expression still depends on a name the environment does not settle."""

    message: str
    offset: int | None = None


def _resolve_defined(tokens: list[Token], environment: MacroLookup) -> list[Token] | _Unresolved:
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
        elif (
            index + 3 < len(tokens)
            and tokens[index + 1].text == "("
            and tokens[index + 2].kind == "identifier"
            and tokens[index + 3].text == ")"
        ):
            name, end, index = tokens[index + 2].text, tokens[index + 3].end, index + 4
        else:
            raise NumericConditionError("malformed defined operator")
        defined = environment.get(name).defined
        if defined is None:
            return _Unresolved(f"definedness of {name!r} is unknown", token.start)
        result.append(Token("1" if defined else "0", "number", token.start, end, generated=True))
    return result


def _integer(text: str) -> _Value:
    match = _INTEGER.fullmatch(text)
    if match is None:
        raise NumericConditionError(f"unsupported integer constant {text!r}")
    body = match.group("body").replace("'", "")
    suffix = (match.group("suffix") or "").lower()
    base = (
        16
        if body.lower().startswith("0x")
        else 2
        if body.lower().startswith("0b")
        else 8
        if len(body) > 1 and body.startswith("0")
        else 10
    )
    try:
        value = int(body, base)
    except ValueError:
        # CPython refuses to convert a decimal string of thousands of digits.
        raise NumericConditionError(
            "integer constant exceeds concrete-evaluation range", kind=FailureKind.OVERFLOW
        ) from None
    if value > _UINT_MASK:
        raise NumericConditionError(
            "integer constant exceeds concrete-evaluation range", kind=FailureKind.OVERFLOW
        )
    if "u" in suffix:
        return _unsigned(value)
    if value <= _SIGNED_MAX:
        return _signed(value)
    # For non-decimal constants the preprocessor may select uintmax_t when the
    # value is not representable by intmax_t. Decimal overflow is kept explicit.
    if base != 10:
        return _unsigned(value)
    raise NumericConditionError(
        "decimal integer constant exceeds signed concrete-evaluation range",
        kind=FailureKind.OVERFLOW,
    )


def _numeric_character(value: int, text: str) -> _Value:
    # Every conforming C implementation can represent 0..127 in unsigned char,
    # and values in that range do not depend on plain-char signedness when mapped
    # to the #if intmax_t model. Above that boundary both the numeric value and
    # even zero/nonzero truth may depend on target char width/conversion rules.
    if value <= 0x7F:
        return _signed(value)
    return _Value(
        None,
        False,
        reason=(f"ordinary character constant {text!r} depends on target char signedness/width"),
    )


def _character(text: str) -> _Value:
    match = _CHARACTER.fullmatch(text)
    if match is None:
        raise NumericConditionError(f"unsupported literal {text!r} in concrete condition")
    prefix = match.group("prefix")
    if prefix:
        raise NumericConditionError(
            f"prefixed character constant {text!r} requires a configured source-language/encoding model"
        )

    body = match.group("body")
    if not body:
        raise NumericConditionError("empty character constant in concrete condition")

    units: list[tuple[str, int | str]] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            if char in "\r\n":
                raise NumericConditionError("newline in character constant")
            if char in _BASIC_RAW_CHARACTERS:
                units.append(("character", char))
            else:
                units.append(("nonbasic", char))
            index += 1
            continue

        if index + 1 >= len(body):
            raise NumericConditionError("unterminated escape in character constant")
        escaped = body[index + 1]
        if escaped in _SIMPLE_ESCAPES:
            units.append(("character", _SIMPLE_ESCAPES[escaped]))
            index += 2
            continue
        if escaped in "01234567":
            end = index + 2
            while end < len(body) and end < index + 4 and body[end] in "01234567":
                end += 1
            units.append(("number", int(body[index + 1 : end], 8)))
            index = end
            continue
        if escaped == "x":
            end = index + 2
            while end < len(body) and body[end] in "0123456789abcdefABCDEF":
                end += 1
            if end == index + 2:
                raise NumericConditionError(
                    "hex escape in character constant requires at least one digit"
                )
            units.append(("number", int(body[index + 2 : end], 16)))
            index = end
            continue
        if escaped in {"u", "U"}:
            digits = 4 if escaped == "u" else 8
            end = index + 2 + digits
            spelling = body[index + 2 : end]
            if (
                end > len(body)
                or len(spelling) != digits
                or any(part not in "0123456789abcdefABCDEF" for part in spelling)
            ):
                raise NumericConditionError(
                    "malformed universal character name in character constant"
                )
            codepoint = int(spelling, 16)
            if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                raise NumericConditionError(
                    "invalid universal character name in character constant"
                )
            units.append(("universal", codepoint))
            index = end
            continue
        raise NumericConditionError(
            f"unsupported escape sequence \\{escaped} in character constant"
        )

    if len(units) != 1:
        return _Value(
            None,
            False,
            reason=f"multicharacter constant {text!r} has implementation-defined value",
        )

    kind, payload = units[0]
    if kind == "number":
        return _numeric_character(int(payload), text)
    if kind == "character":
        return _Value(
            None,
            False,
            True,
            str(payload),
            "ordinary character constant numeric value depends on the implementation character set",
        )
    return _Value(
        None,
        False,
        character=f"U+{int(payload):04X}" if kind == "universal" else str(payload),
        reason=(
            "ordinary non-basic character constant value depends on the implementation character set"
        ),
    )


def _divide(left: int, right: int) -> int:
    if right == 0:
        raise NumericConditionError(
            "division by zero in concrete condition", kind=FailureKind.DIVISION_BY_ZERO
        )
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
            raise NumericConditionError(
                "expression nesting exceeds concrete-evaluation bound",
                kind=FailureKind.NESTING_BOUND,
            )

    def _leave(self) -> None:
        self.depth -= 1

    def here(self) -> int | None:
        """Offset of the token the parser last consumed (the one most errors concern)."""
        return self.tokens[self.index - 1].start if self.index else None

    def parse(self) -> _Value:
        value = self.conditional(True)
        leftover = self.peek()
        if leftover is not None:
            raise NumericConditionError(
                f"unsupported token {leftover.text!r} in concrete condition", leftover.start
            )
        return value

    def primary(self, evaluate: bool) -> _Value:
        if self.take("(") is not None:
            self._enter()
            try:
                value = self.conditional(evaluate)
            finally:
                self._leave()
            if self.take(")") is None:
                following = self.peek()
                raise NumericConditionError(
                    "expected ')' in concrete condition",
                    following.start if following is not None else self.here(),
                )
            return value
        token = self.take()
        if token is None:
            raise NumericConditionError("expected operand in concrete condition")
        if token.kind == "number":
            return _integer(token.text)
        if token.kind == "literal" and "'" in token.text:
            return replace(_character(token.text), origin=token.start)
        if token.kind == "identifier":
            if token.text in token.hidden:
                # Expansion left this name alone because it was already being replaced
                # (a self-referential macro). It is a plain identifier from now on, and
                # every identifier left in a controlling expression is 0 (C11 6.10.1).
                return _Value(0)
            raise NumericConditionError(
                f"unresolved identifier {token.text!r}",
                token.start,
                FailureKind.UNRESOLVED_IDENTIFIER,
            )
        raise NumericConditionError(
            f"unsupported token {token.text!r} in concrete condition", token.start
        )

    def unary(self, evaluate: bool) -> _Value:
        head = self.peek()
        if head is not None and head.text in {"+", "-", "!", "~"}:
            self.take()
            op = head.text
            self._enter()
            try:
                value = self.unary(evaluate)
            finally:
                self._leave()
            if op == "!":
                return _Value(int(not value.truth())) if evaluate else _Value(None)
            if op == "+":
                return value
            if not evaluate:
                return _Value(None, value.unsigned)
            if value.value is None:
                return _Value(
                    None,
                    value.unsigned,
                    value.truth_value if op == "-" else None,
                    reason=value.reason,
                )
            if op == "-":
                return _unsigned(-value.value) if value.unsigned else _signed(-value.value)
            return _unsigned(~value.value) if value.unsigned else _signed(~value.value)
        return self.primary(evaluate)

    def binary(
        self,
        operand: Callable[[bool], _Value],
        operators: set[str],
        evaluate: bool,
        apply: Callable[[str, _Value, _Value, bool], _Value],
    ) -> _Value:
        value = operand(evaluate)
        while (head := self.peek()) is not None and head.text in operators:
            self.take()
            op = head.text
            right = operand(evaluate)
            value = apply(op, value, right, evaluate)
        return value

    def multiplicative(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value, active: bool) -> _Value:
            left, right, unsigned = _convert(left, right)
            if not active:
                return _Value(None, unsigned)
            left_value, right_value = _numeric(left), _numeric(right)
            if op == "*":
                result = left_value * right_value
            elif unsigned:
                if right_value == 0:
                    raise NumericConditionError(
                        "division by zero in concrete condition",
                        kind=FailureKind.DIVISION_BY_ZERO,
                    )
                result = left_value // right_value if op == "/" else left_value % right_value
            else:
                quotient = _divide(left_value, right_value)
                result = quotient if op == "/" else left_value - quotient * right_value
            return _unsigned(result) if unsigned else _signed(result)

        return self.binary(self.unary, {"*", "/", "%"}, evaluate, apply)

    def additive(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value, active: bool) -> _Value:
            left, right, unsigned = _convert(left, right)
            if not active:
                return _Value(None, unsigned)
            left_value, right_value = _numeric(left), _numeric(right)
            result = left_value + right_value if op == "+" else left_value - right_value
            return _unsigned(result) if unsigned else _signed(result)

        return self.binary(self.multiplicative, {"+", "-"}, evaluate, apply)

    def shift(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value, active: bool) -> _Value:
            if not active:
                return _Value(None, left.unsigned)
            left_value, right_value = _numeric(left), _numeric(right)
            if right_value < 0 or right_value > _MAX_SHIFT:
                raise NumericConditionError(
                    "shift count exceeds concrete-evaluation bound", kind=FailureKind.OVERFLOW
                )
            if op == "<<":
                result = left_value << right_value
                return _unsigned(result) if left.unsigned else _signed(result)
            return (
                _unsigned(left_value >> right_value)
                if left.unsigned
                else _signed(left_value >> right_value)
            )

        return self.binary(self.additive, {"<<", ">>"}, evaluate, apply)

    def relational(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value, active: bool) -> _Value:
            if not active:
                return _Value(None)
            left, right, _ = _convert(left, right)
            left_value, right_value = _numeric(left), _numeric(right)
            return _Value(
                int(
                    {
                        "<": left_value < right_value,
                        "<=": left_value <= right_value,
                        ">": left_value > right_value,
                        ">=": left_value >= right_value,
                    }[op]
                )
            )

        return self.binary(self.shift, {"<", "<=", ">", ">="}, evaluate, apply)

    def equality(self, evaluate: bool) -> _Value:
        def apply(op: str, left: _Value, right: _Value, active: bool) -> _Value:
            if not active:
                return _Value(None)
            left, right, _ = _convert(left, right)
            if left.value is not None and right.value is not None:
                equal = left.value == right.value
            elif left.character is not None and right.character is not None:
                equal = left.character == right.character
            elif left.value == 0 and right.truth_value is not None:
                equal = not right.truth_value
            elif right.value == 0 and left.truth_value is not None:
                equal = not left.truth_value
            else:
                raise NumericConditionError(
                    _unknown_reason(left, right),
                    _unknown_origin(left, right),
                    FailureKind.IMPLEMENTATION_DEFINED,
                )
            return _Value(int(equal if op == "==" else not equal))

        return self.binary(self.relational, {"==", "!="}, evaluate, apply)

    def bitand(self, evaluate: bool) -> _Value:
        return self.binary(self.equality, {"&"}, evaluate, self._bitwise)

    def bitxor(self, evaluate: bool) -> _Value:
        return self.binary(self.bitand, {"^"}, evaluate, self._bitwise)

    def bitor(self, evaluate: bool) -> _Value:
        return self.binary(self.bitxor, {"|"}, evaluate, self._bitwise)

    @staticmethod
    def _bitwise(op: str, left: _Value, right: _Value, active: bool) -> _Value:
        left, right, unsigned = _convert(left, right)
        if not active:
            return _Value(None, unsigned)
        left_value, right_value = _numeric(left), _numeric(right)
        result = {
            "&": left_value & right_value,
            "^": left_value ^ right_value,
            "|": left_value | right_value,
        }[op]
        return _unsigned(result) if unsigned else _signed(result)

    def logical_and(self, evaluate: bool) -> _Value:
        value = self.bitor(evaluate)
        while self.take("&&") is not None:
            left_truth = value.truth() if evaluate else False
            right = self.bitor(evaluate and left_truth)
            value = _Value(int(left_truth and right.truth())) if evaluate else _Value(None)
        return value

    def logical_or(self, evaluate: bool) -> _Value:
        value = self.logical_and(evaluate)
        while self.take("||") is not None:
            left_truth = value.truth() if evaluate else False
            right = self.logical_and(evaluate and not left_truth)
            value = _Value(int(left_truth or right.truth())) if evaluate else _Value(None)
        return value

    def conditional(self, evaluate: bool) -> _Value:
        condition = self.logical_or(evaluate)
        if self.take("?") is None:
            return condition

        condition_truth = condition.truth() if evaluate else False
        self._enter()
        try:
            when_true = self.conditional(evaluate and condition_truth)
            if self.take(":") is None:
                raise NumericConditionError("expected ':' in conditional expression")
            when_false = self.conditional(evaluate and not condition_truth)
        finally:
            self._leave()

        when_true, when_false, unsigned = _convert(when_true, when_false)
        if not evaluate:
            return _Value(None, unsigned)
        return when_true if condition_truth else when_false


@dataclass(frozen=True)
class IntegerEvaluation:
    """The outcome of evaluating an integer expression.

    A concrete result has ``value`` (and ``unsigned`` says whether it is a
    ``uintmax_t``). Otherwise ``failure`` says why there is no value, ``message``
    describes it, and ``offset`` is the offset in the expression text of the token it
    concerns, when one is known.
    """

    value: int | None = None
    unsigned: bool = False
    failure: FailureKind | None = None
    message: str | None = None
    offset: int | None = None

    @property
    def concrete(self) -> bool:
        return self.value is not None


def _evaluate(
    text: str,
    environment: MacroLookup,
    expansion: Expansion,
    budget: AnalysisBudget,
    unsupported_identifiers: frozenset[str],
) -> tuple[_Parser, _Value] | _Unresolved:
    """Expand and parse ``text``; the caller decides what to ask of the value.

    Raises ``NumericConditionError`` (with ``kind``, and an offset when one is known) for
    syntax or arithmetic that concrete evaluation does not model, and ``ExpansionError``
    when expansion itself fails.
    """
    original = [t for t in tokenize(text) if t.kind not in {"space", "comment"}]
    resolved = _resolve_defined(original, environment)
    if isinstance(resolved, _Unresolved):
        return resolved
    expanded = expansion._expand(resolved, environment)
    for token in expanded:
        if (
            token.kind == "identifier"
            and token.text in unsupported_identifiers
            and environment.get(token.text).definition is None
        ):
            expansion.current_offset = token.start
            raise ExpansionError(
                f"predefined macro {token.text} is not supported during concrete preprocessing"
            )
    significant = [token for token in expanded if token.kind != "empty"]
    parser = _Parser(significant, budget)
    try:
        return parser, parser.parse()
    except RecursionError as error:
        raise NumericConditionError(
            "expression nesting exceeds concrete-evaluation bound",
            parser.here(),
            FailureKind.NESTING_BOUND,
        ) from error
    except NumericConditionError as error:
        if error.kind is FailureKind.UNRESOLVED_IDENTIFIER:
            return _Unresolved(str(error), error.offset)
        if error.offset is None:
            error.offset = parser.here()
        raise


def evaluate_numeric_condition(
    text: str,
    environment: MacroLookup,
    expansion: Expansion,
    budget: AnalysisBudget,
    *,
    unsupported_identifiers: frozenset[str] = frozenset(),
) -> bool | None:
    """Evaluate a macro-expanded integer condition, or return None if still unknown.

    ``unsupported_identifiers`` is checked after standards-style macro expansion so
    callers can distinguish an unmodeled predefined macro from an ordinary unresolved
    identifier, including when another macro expands to the predefined name.
    """
    outcome = _evaluate(text, environment, expansion, budget, unsupported_identifiers)
    if isinstance(outcome, _Unresolved):
        return None
    parser, value = outcome
    try:
        return value.truth()
    except NumericConditionError as error:
        if error.offset is None:
            error.offset = parser.here()
        raise


def evaluate_integer_expression(
    text: str,
    environment: MacroLookup,
    expansion: Expansion,
    budget: AnalysisBudget,
    *,
    unsupported_identifiers: frozenset[str] = frozenset(),
) -> IntegerEvaluation:
    """Evaluate a macro-expanded integer expression to its value.

    The grammar, conversions and bounds are exactly those of
    :func:`evaluate_numeric_condition`; this returns the integer (and whether it is
    unsigned) where that returns only its truth. Anything that prevents a value,
    including an unresolved identifier, comes back as a result with ``failure`` set
    rather than as an exception. ``ExpansionError`` and an exhausted ``budget`` still
    propagate, as they do for conditions.
    """
    try:
        outcome = _evaluate(text, environment, expansion, budget, unsupported_identifiers)
    except NumericConditionError as error:
        return IntegerEvaluation(failure=error.kind, message=str(error), offset=error.offset)
    if isinstance(outcome, _Unresolved):
        return IntegerEvaluation(
            failure=FailureKind.UNRESOLVED_IDENTIFIER,
            message=outcome.message,
            offset=outcome.offset,
        )
    parser, value = outcome
    if value.value is None:
        message = (
            value.reason or "expression value is implementation-defined in concrete evaluation"
        )
        return IntegerEvaluation(
            failure=FailureKind.IMPLEMENTATION_DEFINED,
            message=message,
            offset=value.origin if value.origin is not None else parser.here(),
        )
    return IntegerEvaluation(value=value.value, unsigned=value.unsigned)


__all__ = [
    "FailureKind",
    "IntegerEvaluation",
    "NumericConditionError",
    "evaluate_integer_expression",
    "evaluate_numeric_condition",
]
