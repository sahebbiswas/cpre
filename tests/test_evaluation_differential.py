"""Evaluated values agree with GCC and Clang (issue #220).

``evaluate_expression`` must give the value a real preprocessor computes. For each
expression cpre reports as concrete, a probe ``#if (EXPR) == VALUE`` is run through every
reference compiler the existing harness found (see ``test_compiler_differential``), and a
second probe checks the signedness of the result. An unevaluable result is never compared:
the fixtures only assert its reason. See ``docs/compiler-differential.md`` for the cases,
the seeded generator and how to rerun a seed.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_compiler_differential import (
    _NO_REFERENCE,
    BASE_FLAGS,
    REFERENCES,
    TIMEOUT,
    Reference,
    tokens,
)

import cpre

INT64_MAX = 9223372036854775807
INT64_MIN = -INT64_MAX - 1
UINT64_MAX = (1 << 64) - 1
MASK = UINT64_MAX


@dataclass(frozen=True)
class Define:
    name: str
    replacement: str
    parameters: tuple[str, ...] | None = None

    def source(self) -> str:
        head = (
            self.name if self.parameters is None else f"{self.name}({', '.join(self.parameters)})"
        )
        return f"#define {head} {self.replacement}"

    def macro(self) -> cpre.MacroDefinition:
        return cpre.MacroDefinition(self.name, self.replacement, self.parameters)


def configuration(defines: list[Define]) -> cpre.MacroConfiguration:
    return cpre.MacroConfiguration(definitions=[define.macro() for define in defines])


def literal(value: int, unsigned: bool) -> str:
    """``value`` written so the preprocessor reads it as the same value and type."""
    if unsigned:
        return f"{value}u"
    if value == INT64_MIN:
        return "(-9223372036854775807 - 1)"
    return f"({value})"


# --- Probing a reference compiler ---------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    index: int
    expression: str
    value: int
    unsigned: bool

    def source(self) -> str:
        return (
            f"#if ({self.expression}) == {literal(self.value, self.unsigned)}\n"
            f"eq{self.index}\n#endif\n"
            f"#if 0 * ({self.expression}) - 1 < 0\nsigned{self.index}\n#endif\n"
        )

    def holds(self, found: frozenset[str]) -> bool:
        return f"eq{self.index}" in found and (f"signed{self.index}" in found) != self.unsigned


def _run(reference: Reference, source: str) -> tuple[frozenset[str], str | None]:
    """Preprocess ``source``; the identifiers that survive, or the compiler's error."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "probe.c")
        path.write_text(source, encoding="utf-8")
        try:
            done = subprocess.run(
                [*reference.command, *BASE_FLAGS, "-std=c11", str(path)],
                capture_output=True,
                check=False,
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{reference.family} did not finish a probe within {TIMEOUT} seconds")
    if done.returncode != 0:
        return frozenset(), done.stderr.decode("utf-8", "replace").strip()
    return frozenset(tokens(done.stdout.decode("utf-8"))), None


def disagreements(reference: Reference, defines: list[Define], probes: list[Probe]) -> list[str]:
    """One report per probe whose value or signedness the compiler does not confirm."""
    header = "".join(f"{define.source()}\n" for define in defines)
    found, error = _run(reference, header + "".join(probe.source() for probe in probes))
    if error is None:
        failed = [probe for probe in probes if not probe.holds(found)]
        results = {probe.index: (probe.holds(found), None) for probe in probes}
    else:
        # One probe broke the whole file; run them one at a time to name it.
        results = {}
        for probe in probes:
            found, error = _run(reference, header + probe.source())
            results[probe.index] = (error is None and probe.holds(found), error)
        failed = [probe for probe in probes if not results[probe.index][0]]
    return [_report(reference, defines, probe, results[probe.index][1]) for probe in failed]


def _report(reference: Reference, defines: list[Define], probe: Probe, error: str | None) -> str:
    kind = "unsigned" if probe.unsigned else "signed"
    lines = [
        f"{reference.family} does not confirm cpre: ({probe.expression}) is "
        f"{probe.value} ({kind}) according to cpre",
        *(f"  {define.source()}" for define in _needed(defines, probe.expression)),
    ]
    if error is not None:
        lines.append(f"  {reference.family} rejected it: {error}")
    else:
        lines.append(f"  {reference.family} gave a different value or signedness")
    return "\n".join(lines)


def _needed(defines: list[Define], expression: str) -> list[Define]:
    """The definitions ``expression`` reaches, in definition order: a minimal reproduction."""
    by_name = {define.name: define for define in defines}
    reached: set[str] = set()
    pending = [expression]
    while pending:
        for name in re.findall(r"[A-Za-z_]\w*", pending.pop()):
            if name in by_name and name not in reached:
                reached.add(name)
                pending.append(by_name[name].replacement)
    return [define for define in defines if define.name in reached]


def checked(defines: list[Define], expressions: list[str]) -> tuple[list[Probe], list[str]]:
    """Ask cpre for each value: the concrete ones as probes, the rest as skipped reasons."""
    config = configuration(defines)
    probes: list[Probe] = []
    unevaluable: list[str] = []
    for expression in expressions:
        result = cpre.evaluate_expression(expression, config)
        if result.concrete:
            assert result.value is not None and result.unsigned is not None
            probes.append(Probe(len(probes), expression, result.value, result.unsigned))
        else:
            unevaluable.append(expression)
    return probes, unevaluable


# --- Fixtures -----------------------------------------------------------------------------

DEFINES = [
    Define("W", "3"),
    Define("H", "4"),
    Define("PAD", "2"),
    Define("AREA", "(W * H + PAD)"),
    Define("SUM", "W + H"),
    Define("BIG", "9223372036854775807"),
    Define("LOW", "(-9223372036854775807 - 1)"),
    Define("ALL", "0xffffffffffffffffu"),
    Define("SQ", "((x) * (x))", ("x",)),
    Define("SQ_RAW", "x * x", ("x",)),
    Define("SEL", "((c) ? (a) : (b))", ("c", "a", "b")),
]


def wrap(value: int) -> int:
    """``value`` as a 64-bit unsigned number."""
    return value & MASK


def trunc_div(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return quotient if (a < 0) == (b < 0) else -quotient


def trunc_mod(a: int, b: int) -> int:
    return a - b * trunc_div(a, b)


# (expression, value, unsigned): the value the C standard gives and every compiler computes.
VALUES: list[tuple[str, int, bool]] = [
    # Every arithmetic operator
    ("1 + 2", 3, False),
    ("5 - 8", -3, False),
    ("6 * 7", 42, False),
    ("7 / 2", 3, False),
    ("7 % 3", 1, False),
    ("-(-3)", 3, False),
    ("+4", 4, False),
    ("~5", -6, False),
    ("!0", 1, False),
    ("!7", 0, False),
    ("5 & 3", 1, False),
    ("5 | 3", 7, False),
    ("5 ^ 3", 6, False),
    ("1 << 10", 1024, False),
    ("1024 >> 3", 128, False),
    ("3 < 4", 1, False),
    ("4 <= 3", 0, False),
    ("3 == 3 && 4 != 5", 1, False),
    ("0 || 0", 0, False),
    ("1 ? 2 : 3", 2, False),
    ("0 ? 2 : 3", 3, False),
    ("1 + 2 * 3 - 4 / 2 % 2", 7, False),
    ("(1 + 2) * (3 - 4)", -3, False),
    ("2 * 3 % 4", 2, False),
    ("1 + 2 << 3", 24, False),
    ("1 << 2 + 1", 8, False),
    ("6 & 3 == 3", 0, False),
    ("1 | 2 ^ 3 & 4", 3, False),
    ("1 || 0 && 0", 1, False),
    ("0 ? 1 : 1 ? 2 : 3", 2, False),
    # Short-circuiting skips a division by zero
    ("0 && 1 / 0", 0, False),
    ("1 || 1 % 0", 1, False),
    ("1 ? 5 : 1 / 0", 5, False),
    ("0 ? 1 / 0 : 6", 6, False),
    # Truncating division and the sign of the remainder
    *(
        (f"({a}) {op} ({b})", fn(a, b), False)
        for a in (7, -7)
        for b in (2, -2)
        for op, fn in (("/", trunc_div), ("%", trunc_mod))
    ),
    # Negative operands and arithmetic shifts. A left shift of a negative value and
    # `LOW % -1` are undefined in C proper; GCC and Clang both give the value below in
    # `#if`, and cpre documents that model (conditional-expressions.md), so they are here
    # as a check on the shared behaviour, not as a statement of what the standard promises.
    ("-8 >> 1", -4, False),
    ("-1 >> 63", -1, False),
    ("-9 >> 2", -3, False),
    ("-1 << 1", -2, False),
    ("-3 << 2", -12, False),
    ("-5 & 3", 3, False),
    ("-5 | 3", -5, False),
    ("-5 ^ 3", -8, False),
    ("-2 * -3", 6, False),
    ("-2 - -3", 1, False),
    # int64 boundaries
    ("9223372036854775807", INT64_MAX, False),
    ("-9223372036854775807 - 1", INT64_MIN, False),
    ("BIG + 0", INT64_MAX, False),
    ("LOW + BIG", -1, False),
    ("BIG - BIG", 0, False),
    ("LOW / 1", INT64_MIN, False),
    ("LOW % -1", 0, False),
    ("LOW % 1", 0, False),
    ("BIG * -1", -INT64_MAX, False),
    ("-BIG", -INT64_MAX, False),
    ("LOW + 1", INT64_MIN + 1, False),
    ("BIG / -1", -INT64_MAX, False),
    ("BIG % -1", 0, False),
    ("LOW >> 63", -1, False),
    ("BIG >> 62", 1, False),
    ("1 << 62", 1 << 62, False),
    ("~LOW", INT64_MAX, False),
    ("LOW < BIG", 1, False),
    # Unsigned conversion and wraparound
    ("0u - 1", UINT64_MAX, True),
    ("-1 > 0u", 1, False),
    ("-1 < 0", 1, False),
    ("ALL + 1u", 0, True),
    ("ALL / 2u", UINT64_MAX // 2, True),
    ("ALL % 2u", 1, True),
    ("ALL * ALL", 1, True),
    ("~0u", UINT64_MAX, True),
    ("~ALL", 0, True),
    ("-1 + 0u", UINT64_MAX, True),
    ("1u << 63", 1 << 63, True),
    ("ALL >> 63", 1, True),
    ("ALL >> 1", UINT64_MAX >> 1, True),
    ("-7 % 3u", wrap(-7) % 3, True),
    ("-7 / 3u", wrap(-7) // 3, True),
    ("7u - 8", wrap(-1), True),
    ("1 ? -1 : 0u", UINT64_MAX, True),
    ("0 ? 0u : -1", UINT64_MAX, True),
    ("(1 ? -1 : 0u) > 0", 1, False),
    ("-1 >> 1u", -1, False),
    ("0xffffffffffffffffu == -1", 1, False),
    ("0x7fffffffffffffff + 0", INT64_MAX, False),
    ("0x8000000000000000", 1 << 63, True),
    ("9223372036854775808u", 1 << 63, True),
    ("0b101 + 010 + 0x10", 29, False),
    ("10ul + 1ll", 11, True),
    ("10ll + 1", 11, False),
    # Nested and function-like macros, and use-site precedence
    ("AREA", 14, False),
    ("AREA % 5", 4, False),
    ("AREA * 2 - SUM", 29, False),
    ("SUM * 2", 11, False),
    ("2 * SUM", 10, False),
    ("SUM % 4", 3, False),
    ("-SUM", 1, False),
    ("SQ(W)", 9, False),
    ("SQ(W + 1)", 16, False),
    ("SQ_RAW(W + 1)", 7, False),
    ("SQ(SQ(2))", 16, False),
    ("SEL(W > 2, H, 1 / 0)", 4, False),
    ("SEL(0, 1 / 0, AREA)", 14, False),
    ("SQ(AREA) - SQ(SUM)", 147, False),
    ("SEL(1, ALL, 0) + 1", 0, True),
]

# Expressions with no value cpre will claim, and the reason it gives.
UNEVALUABLE: list[tuple[str, cpre.EvaluationReason]] = [
    ("1 / 0", cpre.EvaluationReason.DIVISION_BY_ZERO),
    ("1 % 0", cpre.EvaluationReason.DIVISION_BY_ZERO),
    ("1u / 0u", cpre.EvaluationReason.DIVISION_BY_ZERO),
    ("1 ? 1 / 0 : 2", cpre.EvaluationReason.DIVISION_BY_ZERO),
    ("BIG + 1", cpre.EvaluationReason.OVERFLOW),
    ("LOW - 1", cpre.EvaluationReason.OVERFLOW),
    ("BIG * 2", cpre.EvaluationReason.OVERFLOW),
    ("-LOW", cpre.EvaluationReason.OVERFLOW),
    ("LOW / -1", cpre.EvaluationReason.OVERFLOW),
    ("1 << 63", cpre.EvaluationReason.OVERFLOW),
    ("1 << 64", cpre.EvaluationReason.OVERFLOW),
    ("1 << -1", cpre.EvaluationReason.OVERFLOW),
    ("1 >> 64", cpre.EvaluationReason.OVERFLOW),
    ("18446744073709551616", cpre.EvaluationReason.OVERFLOW),
    ("UNKNOWN + 1", cpre.EvaluationReason.UNRESOLVED_IDENTIFIER),
    ("'a' + 1", cpre.EvaluationReason.IMPLEMENTATION_DEFINED),
    ("'ab'", cpre.EvaluationReason.IMPLEMENTATION_DEFINED),
    ("1 +", cpre.EvaluationReason.UNSUPPORTED_SYNTAX),
    ("1.5 + 1", cpre.EvaluationReason.UNSUPPORTED_SYNTAX),
    ('"text"', cpre.EvaluationReason.UNSUPPORTED_SYNTAX),
    ("(1, 2)", cpre.EvaluationReason.UNSUPPORTED_SYNTAX),
]


def test_fixture_values_are_what_cpre_computes():
    config = configuration(DEFINES)
    for expression, value, unsigned in VALUES:
        result = cpre.evaluate_expression(expression, config)
        assert (result.value, result.unsigned) == (value, unsigned), expression


@pytest.mark.parametrize(("expression", "reason"), UNEVALUABLE, ids=[e for e, _ in UNEVALUABLE])
def test_unevaluable_fixtures_name_their_reason(expression, reason):
    result = cpre.evaluate_expression(expression, configuration(DEFINES))
    assert not result.concrete and result.reason is reason


def _references():
    return REFERENCES or [None]


@pytest.mark.parametrize("reference", _references(), ids=lambda r: r.family if r else "none")
def test_fixtures_agree_with_the_compiler(reference: Reference | None):
    if reference is None:
        pytest.skip(_NO_REFERENCE)
    probes, unevaluable = checked(DEFINES, [expression for expression, _, _ in VALUES])
    assert not unevaluable
    assert not (problems := disagreements(reference, DEFINES, probes)), "\n\n".join(problems)


# --- Seeded random expressions ------------------------------------------------------------

SEED_ENV = "CPRE_DIFFERENTIAL_SEED"
COUNT_ENV = "CPRE_DIFFERENTIAL_COUNT"
DEFAULT_SEEDS = tuple(range(1, 9))
DEFAULT_COUNT = 60
MIN_CONCRETE_SHARE = 0.4

LITERALS = [
    "0", "1", "2", "3", "5", "7", "10", "100", "255", "1000", "65535", "2147483647",
    "4294967296", "4611686018427387904", "9223372036854775807", "0x7f", "010", "0b101",
    "0u", "1u", "3u", "4294967295u", "18446744073709551615u", "0xffffffffffffffffu",
]  # fmt: skip
BINARY = ["+", "-", "*", "/", "%", "&", "|", "^", "==", "!=", "<", ">", "<=", ">=", "&&", "||"]
HELPERS = [
    Define("F_ADD", "((a) + (b))", ("a", "b")),
    Define("F_MUL_RAW", "a * b", ("a", "b")),
    Define("F_NEG", "(-(a))", ("a",)),
    Define("F_SEL", "((c) ? (a) : (b))", ("c", "a", "b")),
]


class Generator:
    """Arithmetic expressions over a few configured macros, bounded in depth and size."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.names: list[str] = []
        self.defines: list[Define] = list(HELPERS)

    def child(self, depth: int) -> str:
        text = self.expression(depth)
        # Bare children leave the grouping to C's precedence rules.
        return text if self.rng.random() < 0.3 and " " not in text else f"({text})"

    def expression(self, depth: int, *, compound: bool = False) -> str:
        rng = self.rng
        roll = rng.random()
        if depth <= 0 or (roll < 0.25 and not compound):
            if self.names and rng.random() < 0.4:
                return rng.choice(self.names)
            return rng.choice(LITERALS)
        if roll < 0.70:
            op = rng.choice(BINARY)
            right = self.child(depth - 1)
            if op in ("/", "%"):
                # A skipped division by an unsigned zero is typed differently by GCC
                # (if_skipped_unsigned_division_by_zero.c); keep divisors nonzero here.
                right = f"({right} | 1)"
            return f"{self.child(depth - 1)} {op} {right}"
        if roll < 0.78:
            op = rng.choice(["<<", ">>"])
            return f"{self.child(depth - 1)} {op} {rng.randint(0, 40)}"
        if roll < 0.86:
            return f"{rng.choice(['-', '~', '!', '+'])}({self.expression(depth - 1)})"
        if roll < 0.92:
            return f"{self.child(depth - 1)} ? {self.child(depth - 1)} : {self.child(depth - 1)}"
        name, arity = rng.choice([("F_ADD", 2), ("F_MUL_RAW", 2), ("F_NEG", 1), ("F_SEL", 3)])
        return f"{name}({', '.join(self.expression(depth - 1) for _ in range(arity))})"

    def build(self, count: int) -> list[str]:
        for index in range(self.rng.randint(2, 5)):
            name = f"M{index}"
            # A macro without a value would starve every expression that uses it, so
            # redraw until the body evaluates; the fallback is always valid.
            for _ in range(20):
                body = self.expression(2)
                if self.rng.random() < 0.5:
                    body = f"({body})"
                if cpre.evaluate_expression(body, configuration(self.defines)).concrete:
                    break
            else:
                body = str(index + 1)
            self.defines.append(Define(name, body))
            self.names.append(name)
        return [self.expression(self.rng.randint(1, 4), compound=True) for _ in range(count)]


def _seeds() -> tuple[int, ...]:
    chosen = os.environ.get(SEED_ENV)
    return (int(chosen),) if chosen else DEFAULT_SEEDS


def _count() -> int:
    count = int(os.environ.get(COUNT_ENV, DEFAULT_COUNT))
    if count < 1:
        raise ValueError(f"{COUNT_ENV} must be a positive number of expressions, not {count}")
    return count


@pytest.mark.parametrize("seed", _seeds())
def test_random_expressions_agree_with_the_compiler(seed: int):
    generator = Generator(seed)
    expressions = generator.build(_count())
    probes, unevaluable = checked(generator.defines, expressions)
    # A generator that mostly produced unevaluable text would check nothing.
    assert len(probes) >= MIN_CONCRETE_SHARE * len(expressions), (
        f"seed {seed}: only {len(probes)} of {len(expressions)} expressions had a value"
    )
    if not REFERENCES:
        pytest.skip(_NO_REFERENCE)
    rerun = (
        f"{SEED_ENV}={seed} {COUNT_ENV}={len(expressions)} "
        "pytest tests/test_evaluation_differential.py -k random"
    )
    for reference in REFERENCES:
        problems = disagreements(reference, generator.defines, probes)
        assert not problems, f"seed {seed} (rerun with: {rerun})\n\n" + "\n\n".join(problems)


@pytest.mark.parametrize("value", ["0", "-5"])
def test_a_non_positive_count_is_rejected(monkeypatch, value):
    monkeypatch.setenv(COUNT_ENV, value)
    with pytest.raises(ValueError, match="positive"):
        _count()
