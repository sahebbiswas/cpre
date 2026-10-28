"""Operator precedence of integer preprocessor expressions.

Shared by the analyses that ask how a replacement list groups with the text around a
use of its macro (``#define M A || B`` makes ``!M`` mean ``!A || B``). Everything works
on token *texts*, so it serves the lexer's tokens and the expression parser's alike.
"""

from __future__ import annotations

from collections.abc import Sequence

# Higher binds tighter. ``?`` stands for the whole conditional operator.
PRECEDENCE: dict[str, int] = {
    "*": 13,
    "/": 13,
    "%": 13,
    "+": 12,
    "-": 12,
    "<<": 11,
    ">>": 11,
    "<": 10,
    "<=": 10,
    ">": 10,
    ">=": 10,
    "==": 9,
    "!=": 9,
    "&": 8,
    "^": 7,
    "|": 6,
    "&&": 5,
    "||": 4,
    "?": 3,
}

# Operators whose result changes with grouping even at equal precedence: ``A - (B - C)``.
NON_ASSOCIATIVE = frozenset({"-", "/", "%", "<<", ">>", "<", "<=", ">", ">=", "==", "!="})

# Operators that truncate: ``A * (B / C)`` is not ``A * B / C``, even though ``*`` and ``/``
# are left-associative at the same precedence.
TRUNCATING = frozenset({"/", "%"})

_PREFIX = frozenset({"!", "~", "+", "-"})


def is_operand_end(text: str) -> bool:
    """Whether a token ending an operand: a name, constant or closing parenthesis."""
    return text == ")" or text[:1].isalnum() or text[:1] in {"_", "'", '"', "."}


def top_level_binary_operators(texts: Sequence[str]) -> list[str]:
    """The binary (and conditional) operators outside every pair of parentheses."""
    found: list[str] = []
    depth = 0
    for index, text in enumerate(texts):
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
        elif depth == 0 and text in PRECEDENCE:
            if text in {"+", "-"} and (index == 0 or not is_operand_end(texts[index - 1])):
                continue  # a prefix sign, not a binary operator
            found.append(text)
    return found


def weakest_operator(operators: Sequence[str]) -> str | None:
    """The operator of lowest precedence, which decides how the whole list groups."""
    return min(operators, key=PRECEDENCE.__getitem__, default=None)


def use_site_conflict(
    replacement: Sequence[str], before: Sequence[str], after: str | None
) -> tuple[str, str, str] | None:
    """How an unparenthesized replacement regroups with its neighbours, or ``None``.

    ``replacement`` is the token texts of the expansion, ``before`` the (up to two) tokens
    in front of it and ``after`` the token after it. A conflict exists when the operator
    next to the replacement binds tighter than the replacement's weakest top-level
    operator, when a prefix operator precedes it, or when it is the right operand of a
    non-associative operator of the same precedence, or of the same precedence when the
    replacement truncates (``2 * Q`` with ``Q`` as ``H / W``). Returns ``(weakest operator, the
    neighbouring operator, "before" or "after")``.
    """
    operators = top_level_binary_operators(replacement)
    weakest = weakest_operator(operators)
    if weakest is None:
        return None
    level = PRECEDENCE[weakest]
    truncating = any(op in TRUNCATING and PRECEDENCE[op] == level for op in operators)
    if before:
        left = before[-1]
        prefix = left in {"!", "~"} or (
            left in {"+", "-"} and (len(before) == 1 or not is_operand_end(before[-2]))
        )
        if prefix:
            return weakest, left, "before"
        if left in PRECEDENCE and left != "?":
            tighter = PRECEDENCE[left] > level
            regroups = PRECEDENCE[left] == level and (left in NON_ASSOCIATIVE or truncating)
            if tighter or regroups:
                return weakest, left, "before"
    if after in PRECEDENCE and after != "?" and PRECEDENCE[after] > level:
        return weakest, after, "after"
    return None
