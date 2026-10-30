"""Reading expanded macros back as expressions stays fast (issue #235).

The algebra is pure and runs on the same subtrees repeatedly. Without remembering results,
a chain of 25 macros that refer to one another took tens of seconds with ``expand_macros``.
"""

from __future__ import annotations

import random

import cpre
import cpre.expressions as expressions
from cpre.expressions import (
    clear_expression_caches,
    conjunction,
    disjunction,
    negate,
    parse_expression,
    simplify,
)
from cpre.model import Conjunction, Disjunction, Negation, Variable


def _chain(count: int, atoms: int = 10, seed: int = 1) -> str:
    rng = random.Random(seed)
    lines = []
    for index in range(count):
        pool = [f"B{i}" for i in range(atoms)] + [f"M{j}" for j in range(index)]
        a, b, c = (rng.choice(pool) for _ in range(3))
        operator, other = rng.choice([("||", "&&"), ("&&", "||")])
        lines.append(f"#define M{index} (({a} {operator} {b}) {other} !{c})")
    return "\n".join(lines) + "\n"


def test_a_long_chain_of_macros_is_expanded_without_repeated_work(monkeypatch):
    """Counts the work instead of timing it, so a slow runner cannot fail it."""
    calls = 0
    real = expressions.format_expression

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(expressions, "format_expression", counted)
    clear_expression_caches()
    results = cpre.analyze_macros(_chain(25), expand_macros=True)
    assert len(results) == 25 and all(result.complete for result in results)
    # Roughly ten thousand with memoization; millions without it.
    assert calls < 200_000


def test_the_algebra_gives_the_same_answer_when_asked_again():
    expression = parse_expression("(A && (B || !C)) || (!A && D) || (B && C)")
    assert simplify(expression) is simplify(expression)
    assert negate(expression) is negate(expression)
    assert simplify(simplify(expression)) == simplify(expression)
    assert negate(negate(expression)) == simplify(expression)


def test_equal_expressions_hash_alike_however_they_were_built():
    a, b, c = Variable("A"), Variable("B"), Variable("C")
    for build in (Conjunction, Disjunction):
        left = build((a, b, c))
        right = build((a, b, c))
        assert left == right and hash(left) == hash(right) == hash(right)
        assert hash(left) != hash(build((a, c, b))) or left == build((a, c, b))
    assert hash(Negation(a)) == hash(Negation(Variable("A")))
    assert {Negation(a), Negation(Variable("A"))} == {Negation(a)}
    assert conjunction(a, b) == conjunction(b, a)
    assert disjunction(a, b) == disjunction(b, a)


def test_a_cached_hash_does_not_change_equality_or_repr():
    node = Conjunction((Variable("A"), Variable("B")))
    before = repr(node)
    hash(node)
    assert repr(node) == before
    assert node == Conjunction((Variable("A"), Variable("B")))
    assert "_hash" not in repr(node)


def test_the_caches_are_small_and_can_be_cleared():
    assert expressions._CACHE_SIZE <= 1 << 14
    simplify(parse_expression("A && (B || C)"))
    assert simplify.cache_info().currsize > 0
    clear_expression_caches()
    assert simplify.cache_info().currsize == 0
    assert negate.cache_info().currsize == 0
    assert expressions._sort_key.cache_info().currsize == 0
