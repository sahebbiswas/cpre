"""The recomposition stage: suggestions are the shortest proven form and never longer.

Simplifying a macro reads an ROBDD back as an expression, which is often longer than the
definition it came from, most of all with ``expand_macros``. The stage tries other forms
(other atom orders, and the forms with sub-expressions folded back into the macros the
definition refers to) and reports a suggestion only if it has fewer tokens than the
definition.
"""

from __future__ import annotations

import itertools
import random
import re

import pytest

import cpre
import cpre._recomposition as recomposition
from cpre import (
    AnalysisOptions,
    MacroConfiguration,
    MacroDefinition,
    analyze_macro,
    analyze_macros,
    evaluate_expression,
)
from cpre._recomposition import cost, keeps_value
from cpre.expressions import parse_expression, tokens
from cpre.model import Negation, Variable


def _by_name(results):
    return {result.name: result for result in results}


def _expanded(source, **kwargs):
    return _by_name(analyze_macros(source, expand_macros=True, **kwargs))


def _tokens(text):
    return len(tokens(text))


def _logical_tokens(text):
    """Tokens of a replacement without the ``!!`` that keeps a lone identifier 0 or 1."""
    count = _tokens(text)
    return count - 2 if re.fullmatch(r"\(!!\w+\)", text) else count


def _definition_tokens(result):
    """Tokens of the definition, counted with the outer parentheses a rewrite would add."""
    text = result.original_replacement
    wrapped = text.startswith("(") and text.endswith(")") and _balanced_wrap(text)
    return _tokens(text) + (0 if wrapped else 2)


def _balanced_wrap(text):
    depth = 0
    for index, char in enumerate(text):
        depth += char == "("
        depth -= char == ")"
        if depth == 0:
            return index == len(text) - 1
    return False


def _truth(text, values):
    """The value of a Boolean replacement list under ``values``, by Python's own operators."""
    python = text.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    return bool(eval(python, {}, dict(values)))  # noqa: S307 - names and operators only


def _equivalent(left, right, names):
    return all(
        _truth(left, dict(zip(names, combo, strict=True)))
        == _truth(right, dict(zip(names, combo, strict=True)))
        for combo in itertools.product((False, True), repeat=len(names))
    )


def _value(source, name, values):
    config = MacroConfiguration(
        integers=values,
        definitions=[
            MacroDefinition(r.name, r.original_replacement)
            for r in analyze_macros(source)
            if r.definition.parameters is None
        ],
    )
    result = evaluate_expression(name, config)
    assert result.concrete, result.message
    return result.value


# --- Recomposing from the macros a definition refers to ----------------------------------

NEGATION = "#define A (B || C)\n#define E (A && D)\n#define H (!A && !E)\n"


def test_a_suggestion_that_is_not_shorter_is_replaced_by_one_named_after_a_macro():
    """Expanding ``H`` gives ``(!B && !C)``, as long as ``H`` itself; ``(!A)`` is shorter."""
    h = _expanded(NEGATION)["H"]
    assert h.simplified_replacement == "(!A)"
    assert _tokens(h.simplified_replacement) < _tokens(h.original_replacement)
    assert h.is_equivalent is True
    assert set(h.expanded_from) == {"A", "E"}
    assert h.rewritable is False and h.edit is None
    assert h.reason is not None and h.reason.startswith("not rewritten:")


def test_the_unexpanded_run_does_not_see_that_form():
    assert _by_name(analyze_macros(NEGATION))["H"].simplified is False


GROUP = "#define E (B && C)\n#define D (E && (B || X) && F)\n"


def test_operands_that_together_equal_a_macro_are_replaced_by_its_name():
    d = _expanded(GROUP)["D"]
    assert d.simplified_replacement == "(F && E)"
    assert d.expanded_from == ("E",)
    assert d.rewritable is False
    # Expanded and simplified over B, C and F alone, it would be (B && C && F).
    assert _tokens(d.simplified_replacement) < _tokens("(B && C && F)")


@pytest.mark.parametrize("source", [NEGATION, GROUP])
def test_a_recomposed_suggestion_has_the_value_of_the_macro(source):
    name = "H" if source is NEGATION else "D"
    result = _expanded(source)[name]
    replaced = source.replace(result.original_replacement, result.simplified_replacement)
    defined = set(re.findall(r"#define (\w+)", source))
    atoms = sorted(set(re.findall(r"\b[A-Z]\w*", source)) - defined)
    for combo in itertools.product((0, 1, 2), repeat=len(atoms)):
        values = dict(zip(atoms, combo, strict=True))
        assert _value(replaced, name, values) == _value(source, name, values), values


def test_a_form_that_needs_no_expansion_is_an_ordinary_result():
    """``(A && D) || (A && !D)`` is ``A`` whatever ``A`` stands for, so nothing depends on it."""
    f = _expanded("#define A (B || C)\n#define F ((A && D) || (A && !D))\n")["F"]
    assert f.simplified_replacement == "(!!A)"
    assert f.expanded_from == ()
    assert f.expanded_replacement is None and f.expanded_expression is None
    assert f.rewritable is True and f.edit is not None
    assert f.reason is None


def test_without_a_gain_the_expanded_form_is_reported_as_already_simplest():
    k = _expanded("#define A (B || C)\n#define K (A && X)\n")["K"]
    assert k.simplified is False
    assert k.expanded_from == ("A",)
    assert k.reason is not None and "after expanding A" in k.reason


# --- Only shorter suggestions are reported -----------------------------------------------


def _chain(seed, count=6, atoms=4):
    rng = random.Random(seed)
    names = [f"B{i}" for i in range(atoms)]
    lines = []
    for index in range(count):
        pool = names + [f"M{j}" for j in range(index)]
        operands = [
            ("!" if rng.random() < 0.3 else "") + rng.choice(pool)
            for _ in range(rng.choice((3, 4)))
        ]
        operator = rng.choice((" && ", " || "))
        inner = rng.choice(pool)
        lines.append(
            f"#define M{index} ({operator.join(operands)}{operator}({inner} || {rng.choice(pool)}))"
        )
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("expand", [False, True])
def test_every_suggestion_has_fewer_tokens_than_its_definition(seed, expand):
    source = _chain(seed)
    for result in analyze_macros(source, expand_macros=expand):
        if result.simplified_replacement is None:
            continue
        assert _logical_tokens(result.simplified_replacement) < _definition_tokens(result), (
            result.name,
            result.original_replacement,
            result.simplified_replacement,
        )


@pytest.mark.parametrize("seed", range(10))
def test_expanded_suggestions_keep_the_value_of_the_macro(seed):
    source = _chain(seed, count=4, atoms=3)
    names = sorted(set(re.findall(r"B\d", source)))
    for result in analyze_macros(source, expand_macros=True):
        if not result.simplified_replacement:
            continue
        replaced = source.replace(result.original_replacement, result.simplified_replacement, 1)
        for combo in itertools.product((0, 1, 2), repeat=len(names)):
            values = dict(zip(names, combo, strict=True))
            assert _value(replaced, result.name, values) == _value(source, result.name, values), (
                result.name,
                values,
            )


# --- Other atom orders --------------------------------------------------------------------

# The ROBDD read back in the order the atoms first appear leaves this alone.
LONG = "((B || !C) && !A && (!B || !D) || C)"


def test_another_atom_order_shortens_a_macro_the_first_order_leaves_alone():
    result = analyze_macro(MacroDefinition("M", LONG))
    assert result.simplified is True
    assert result.is_equivalent is True
    assert _tokens(result.simplified_replacement) < _tokens(LONG)
    assert _equivalent(LONG, result.simplified_replacement, "ABCD")


def test_the_same_macro_is_left_alone_when_no_other_order_is_tried(monkeypatch):
    monkeypatch.setattr(recomposition, "MAX_REORDER_ATOMS", 0)
    assert analyze_macro(MacroDefinition("M", LONG)).simplified is False


@pytest.mark.parametrize("replacement", ["(A && B)", "(A || B)", "(!A || B)", "(A && !B)"])
def test_small_macros_are_not_given_other_orders(replacement):
    assert analyze_macro(MacroDefinition("M", replacement)).simplified is False


# --- Resource limits ---------------------------------------------------------------------


def test_a_limit_reached_while_looking_for_a_shorter_form_keeps_the_result(monkeypatch):
    monkeypatch.setattr(recomposition, "SEARCH_WORK", 1)
    source = NEGATION
    results = _expanded(source)
    assert all(r.complete for r in results.values())
    assert results["H"].is_equivalent is not False
    plain = analyze_macro(MacroDefinition("M", "((A && B) || (!A && B))"))
    assert plain.complete and plain.simplified_replacement == "(!!B)"


def test_a_tight_work_limit_still_ends_in_an_incomplete_result_not_an_error():
    result = analyze_macro(MacroDefinition("M", LONG), options=AnalysisOptions(max_work=20))
    assert result.complete is False and result.edit is None


# --- Helpers ------------------------------------------------------------------------------


def test_cost_counts_the_tokens_of_the_parenthesized_replacement():
    assert cost(parse_expression("A && B")) == 5
    assert cost(parse_expression("A")) == 3  # the ``!!`` that keeps its value is not counted
    assert cost(parse_expression("!(A || B)")) == 8


def test_keeps_value_wraps_only_a_lone_identifier():
    assert keeps_value(Variable("X")) == Negation(Negation(Variable("X")))
    assert keeps_value(parse_expression("A && B")) == parse_expression("A && B")
    literal = cpre.model.SymbolicLiteral("0")
    assert keeps_value(literal) is literal
