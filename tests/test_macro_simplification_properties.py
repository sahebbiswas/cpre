"""Property-based round-trip tests for macro simplification (issue #97).

Generated Boolean replacement lists are rewritten with ``simplify_macros`` and
both the original and the rewritten source are run through concrete
preprocessing under every configuration of the atoms. The property compares
which branches preprocessing selects, not the text of the replacement.

The contract under test is the documented one: identifiers are Boolean atoms,
so the rewritten macro must select the same branches wherever it is used as a
whole operand (``(M)``), in truth and value contexts, for atoms that are
undefined, 0 or 1. Uses that bind to an unparenthesized replacement, and
integer values other than 0 and 1 in value contexts, are known gaps tracked in
#143 and #144; their regressions are below as strict xfails.

Definedness: every atom is tried undefined as well as defined as 0 or 1, and
two contexts combine the macro with ``defined`` tests on its atoms, where
undefined and 0 differ. The ``defined`` operator inside a replacement list is
outside the documented grammar, so the unsupported-construct property checks
that such replacements are never rewritten.

Runs are derandomized so CI is reproducible, and every regression the search
found is kept as an explicit ``@example``.
"""

from __future__ import annotations

import itertools
import os

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import cpre
from cpre import MacroConfiguration, UnknownNamePolicy

NAMES = ("A", "B", "C")

# Contexts that use the macro as a whole operand. Truth contexts depend only on
# whether the macro is zero; value contexts also depend on its integer value.
TRUTH_CONTEXTS = (
    "(M)",
    "!(M)",
    "(M) && C",
    "!(M) || !C",
    "(M) ? 1 : 0",
    "defined(A) && (M)",
    "!defined B || !(M)",
)
VALUE_CONTEXTS = ("(M) == 1", "(M) + (M) == 2")
CONTEXTS = TRUTH_CONTEXTS + VALUE_CONTEXTS

# None means undefined, which #if evaluates as 0. Other non-zero values add
# nothing here: !, && and || treat every non-zero operand like 1, so they can
# only differ in value contexts, which is the known gap #144.
ATOM_VALUES = (None, 0, 1)

# Each example preprocesses 2 x 27 configurations, so keep normal runs short.
# Set CPRE_PROPERTY_EXAMPLES (for example to 2000) for a deeper local search.
SETTINGS = settings(
    max_examples=int(os.environ.get("CPRE_PROPERTY_EXAMPLES", "25")),
    deadline=None,
    derandomize=True,
)


@st.composite
def _binary(draw, children):
    left = draw(children)
    right = draw(children)
    operator = draw(st.sampled_from(("&&", "||")))
    space = draw(st.sampled_from((" ", "", "  ")))
    return f"{left}{space}{operator}{space}{right}"


# Every spelling of 0 and 1 is a Boolean literal, and in symbolic-literal mode all
# spellings of 0 are the same atom.
LITERALS = ("0", "1", "0x0", "00", "0L", "1u", "0x1", "1UL")


def _replacements(literals=LITERALS):
    """Replacement lists in the supported grammar: atoms, 0/1, !, &&, ||, ()."""
    return st.recursive(
        st.sampled_from(NAMES + literals),
        lambda children: st.one_of(
            children.map(lambda item: f"!{item}"),
            children.map(lambda item: f"({item})"),
            _binary(children),
        ),
        max_leaves=10,
    ).filter(lambda text: any(operator in text for operator in ("&&", "||", "!")))


def _configuration(values):
    return MacroConfiguration(
        integers={name: value for name, value in zip(NAMES, values) if value is not None},
        undefined={name for name, value in zip(NAMES, values) if value is None},
        unknown_names=UnknownNamePolicy.UNDEFINED,
    )


def _source(definition, contexts, newline="\n"):
    lines = ["int before;", definition]
    for number, context in enumerate(contexts):
        lines += [f"#if {context}", f"yes{number}", "#else", f"no{number}", "#endif"]
    return newline.join(lines) + newline


def _selected(source, values):
    result = cpre.preprocess_source(source, configuration=_configuration(values))
    assert result.complete, result.incomplete
    return [line.strip() for line in result.source.splitlines() if line.strip()]


def _assert_same_branches(original, rewritten):
    for values in itertools.product(ATOM_VALUES, repeat=len(NAMES)):
        # Output lines: "int before;", then one branch body per context.
        before = dict(zip(CONTEXTS, _selected(original, values)[1:]))
        after = dict(zip(CONTEXTS, _selected(rewritten, values)[1:]))
        assert before == after, f"configuration {dict(zip(NAMES, values))}"


def _definition(replacement, decoration):
    if decoration == "continuation":
        return f"#define M \\\n    {replacement}"
    return f"#define M {replacement}{decoration}"


DECORATIONS = ("", " /* note */", " // note", "continuation")


@SETTINGS
@given(
    replacement=_replacements(),
    symbolic=st.sampled_from(((), (0,))),
    decoration=st.sampled_from(DECORATIONS),
    newline=st.sampled_from(("\n", "\r\n")),
)
# Regressions and representative cases found while writing these tests.
@example(replacement="A || (!A && B)", symbolic=(), decoration="", newline="\n")
@example(replacement="(0 && A) || B", symbolic=(0,), decoration="", newline="\n")
@example(replacement="!!A && (A)", symbolic=(), decoration=" // note", newline="\r\n")
@example(replacement="!A && A", symbolic=(), decoration="continuation", newline="\n")
@example(replacement="!0 && !A || A", symbolic=(), decoration=" /* note */", newline="\n")
@example(replacement="(0x0 && A) || (00 && B)", symbolic=(0,), decoration="", newline="\n")
def test_rewrite_preserves_preprocessing_for_whole_operand_uses(
    replacement, symbolic, decoration, newline
):
    original = _source(_definition(replacement, decoration), CONTEXTS, newline)

    result = cpre.simplify_macros(original, rewrite=True, symbolic_literals=symbolic)

    assert result.verified
    rewritten = result.rewritten_source
    if not result.rewritten:
        assert rewritten == original
        return
    # Only the replacement list changes: comments, line endings and the
    # surrounding lines are preserved byte for byte.
    [macro] = result.results
    assert macro.simplified_replacement is not None
    assert macro.simplified_replacement in rewritten
    start = original.index(macro.original_replacement)
    end = start + len(macro.original_replacement)
    assert rewritten == original[:start] + macro.simplified_replacement + original[end:]

    _assert_same_branches(original, rewritten)


@SETTINGS
@given(replacement=_replacements(), symbolic=st.sampled_from(((), (0,))))
@example(replacement="A || (!A && B)", symbolic=())
def test_rewrite_is_idempotent_and_matches_the_report(replacement, symbolic):
    source = f"#define M {replacement}\n"

    report = cpre.simplify_macros(source, symbolic_literals=symbolic)
    once = cpre.rewrite_macros(source, symbolic_literals=symbolic)
    twice = cpre.simplify_macros(once, rewrite=True, symbolic_literals=symbolic)

    [macro] = report.results
    if macro.simplified:
        assert once == f"#define M {macro.simplified_replacement}\n"
    else:
        assert once == source
    # A rewritten macro is already in simplest form.
    assert not twice.has_findings
    assert twice.rewritten_source == once


UNSUPPORTED = st.sampled_from(
    (
        "defined(A) && B",
        "defined A || B",
        "A == 1 && B",
        "A + B || C",
        "(A & B) || C",
        "A ? B : C",
        "2 && A",
        "0x2 || A",
        "f(A) && B",
        "A, B || C",
        '"s" && A',
    )
)


@SETTINGS
@given(construct=UNSUPPORTED, symbolic=st.sampled_from(((), (0,))))
def test_unsupported_constructs_are_never_rewritten(construct, symbolic):
    # Constructs outside the documented grammar are skipped, not simplified.
    source = f"#define M ({construct}) || ({construct})\nint x;\n"

    result = cpre.simplify_macros(source, rewrite=True, symbolic_literals=symbolic)

    [macro] = result.results
    assert not macro.candidate
    assert not result.rewritten
    assert result.rewritten_source == source


@pytest.mark.xfail(strict=True, reason="#143: parenthesizing an unparenthesized replacement")
def test_rewrite_of_unparenthesized_replacement_preserves_meaning():
    original = "#define FEAT A || (A && B)\n#if !FEAT\nint off;\n#endif\n"

    rewritten = cpre.rewrite_macros(original)

    # !A || (A && B) is true for A=1, B=1; !(A) is not.
    assert _selected(original, (1, 1, None)) == _selected(rewritten, (1, 1, None))


@pytest.mark.xfail(strict=True, reason="#144: !!X simplifies to (X), losing 0/1 normalization")
def test_rewrite_preserves_integer_value_of_normalized_macro():
    original = "#define M (!!A)\n#if M == 1\nint one;\n#endif\n"

    rewritten = cpre.rewrite_macros(original)

    assert _selected(original, (2, None, None)) == _selected(rewritten, (2, None, None))
