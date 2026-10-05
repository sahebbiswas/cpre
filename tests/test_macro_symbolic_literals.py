"""Tests for opt-in symbolic-literal semantics in macro Boolean simplification (issue #79)."""

import itertools

import pytest

from cpre import AnalysisError, ErrorCode, MacroDefinition, analyze_macro, analyze_macros


def _ordinary(text):
    return analyze_macro(MacroDefinition("FEAT", text))


def _symbolic(text):
    return analyze_macro(MacroDefinition("FEAT", text), symbolic_literals=(0,))


def _evaluate(text, env):
    """Evaluate a supported Boolean replacement list with C truth semantics."""
    python = text.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    return bool(eval(python, {"__builtins__": {}}, dict(env)))


# --- Default behavior is unchanged -----------------------------------------------------------


def test_default_semantics_is_ordinary_and_unchanged():
    result = _ordinary("(0 && A) || B")
    assert result.semantics == "ordinary"
    assert result.symbolic_literals == ()
    assert result.simplified_replacement == "(B)"

    explicit_none = analyze_macro(MacroDefinition("FEAT", "(0 && A) || B"), symbolic_literals=None)
    explicit_empty = analyze_macro(MacroDefinition("FEAT", "(0 && A) || B"), symbolic_literals=())
    assert explicit_none == result
    assert explicit_empty == result


# --- Ordinary vs symbolic-literal comparisons -----------------------------------------------

COMPARISON_CASES = [
    # (replacement, ordinary result, symbolic-zero result); None means "already simplest".
    ("(0 && A) || B", "(B)", None),
    ("(0 && A) || (0 && B)", "(0)", "(0 && (A || B))"),
    ("0 && A", "(0)", None),
    ("(A || 0)", "(A)", None),
    ("0x0 && A || 00 && A", "(0)", "(0 && A)"),
    ("0 || !0", "(1)", "(1)"),
    ("0 && !0", "(0)", "(0)"),
    ("!(!0)", "(0)", "(0)"),
    ("(0 && A) || (!0 && A)", "(A)", "(A)"),
    ("(A && 1) || (0 && A)", "(A)", "(A)"),
    ("(A || (!A && B))", "(A || B)", "(A || B)"),
]


@pytest.mark.parametrize(("text", "ordinary", "symbolic"), COMPARISON_CASES)
def test_ordinary_and_symbolic_literal_semantics_compared(text, ordinary, symbolic):
    plain = _ordinary(text)
    sym = _symbolic(text)

    assert plain.semantics == "ordinary"
    assert sym.semantics == "symbolic-literal"
    assert sym.symbolic_literals == (0,)

    assert plain.simplified_replacement == ordinary
    assert sym.simplified_replacement == symbolic
    assert plain.is_equivalent is True
    assert sym.is_equivalent is True
    if symbolic is None:
        assert sym.simplified is False
        assert "simplest equivalent form" in sym.reason


def test_transient_flag_idiom_is_preserved():
    # The disabled control point survives instead of being folded away.
    result = _symbolic("(0 && A) || B")
    assert result.candidate is True
    assert result.simplified is False
    assert result.simplified_replacement is None


def test_all_zero_spellings_share_one_atom_named_zero():
    # 0x0, 00 and 0u denote the same value, so they are one atom, emitted as "0".
    result = _symbolic("(0x0 && A) || (00 && B) || (0u && A)")
    assert result.simplified_replacement == "(0 && (A || B))"
    assert result.is_equivalent is True


def test_symbolic_zero_does_not_alias_identifiers():
    # A literal atom must never unify with an identifier atom.
    result = _symbolic("(0 && !ZERO) || (0 && ZERO)")
    assert result.simplified_replacement == "(0)"
    other = _symbolic("0 || ZERO")
    assert other.simplified is False


def test_literal_one_keeps_ordinary_meaning_in_symbolic_zero_mode():
    result = _symbolic("(1 && A) || (0 && B)")
    assert result.simplified_replacement == "(0 && B || A)"


# --- Soundness: no ordinary C expression is silently reinterpreted ---------------------------

SOUNDNESS_CASES = [
    "(0 && A) || B",
    "(0 && A) || (0 && B)",
    "(0 && A) || (!0 && A)",
    "(0 || A) && (0 || B)",
    "!(0 && A) || (0 && !B)",
    "((0 && A) || (0 && !A)) && C",
    "(0 && A && B) || (0 && A && !B) || C",
    "0 || !0",
]


@pytest.mark.parametrize("text", SOUNDNESS_CASES)
def test_symbolic_rewrite_is_equivalent_under_ordinary_c_semantics(text):
    # A rewrite proven for every value of the symbolic atom also holds when the
    # literal takes its real C value, so emitted text never changes C meaning.
    result = _symbolic(text)
    rewritten = result.simplified_replacement or result.original_replacement
    for values in itertools.product((False, True), repeat=3):
        env = dict(zip(("A", "B", "C"), values))
        assert _evaluate(rewritten, env) == _evaluate(text, env), (text, rewritten, env)


@pytest.mark.parametrize("text", SOUNDNESS_CASES)
def test_symbolic_rewrite_is_never_more_aggressive_than_ordinary(text):
    plain = _ordinary(text)
    sym = _symbolic(text)
    plain_text = plain.simplified_replacement or plain.original_replacement
    sym_text = sym.simplified_replacement or sym.original_replacement
    for values in itertools.product((False, True), repeat=3):
        env = dict(zip(("A", "B", "C"), values))
        assert _evaluate(plain_text, env) == _evaluate(sym_text, env)


# --- Candidate classification is unaffected ---------------------------------------------------


def test_symbolic_mode_does_not_widen_candidate_set():
    for text in ["0", "(0)", "1024", "(A + 0)", "defined(A) || 0", "foo(0) || A"]:
        plain = _ordinary(text)
        sym = _symbolic(text)
        assert plain.candidate is False
        assert sym.candidate is False
        assert sym.reason == plain.reason
        assert sym.semantics == "symbolic-literal"


# --- Configuration validation ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [(1,), (2,), (0, 1), (-1,)])
def test_unsupported_symbolic_literal_values_are_rejected(bad):
    with pytest.raises(AnalysisError) as excinfo:
        analyze_macro(MacroDefinition("FEAT", "0 && A"), symbolic_literals=bad)
    assert excinfo.value.code == ErrorCode.INVALID_CONFIGURATION
    assert "supported literals: 0" in str(excinfo.value)


@pytest.mark.parametrize("bad", [0, "0", b"0", (True,), ("0",), (0.0,), object()])
def test_malformed_symbolic_literal_policy_is_rejected(bad):
    with pytest.raises(AnalysisError) as excinfo:
        analyze_macro(MacroDefinition("FEAT", "0 && A"), symbolic_literals=bad)
    assert excinfo.value.code == ErrorCode.INVALID_CONFIGURATION


def test_symbolic_literal_policy_is_normalized():
    result = analyze_macro(MacroDefinition("FEAT", "0 && A"), symbolic_literals=[0, 0])
    assert result.symbolic_literals == (0,)
    from_set = analyze_macro(MacroDefinition("FEAT", "0 && A"), symbolic_literals={0})
    assert from_set.symbolic_literals == (0,)


# --- Source-level analysis ---------------------------------------------------------------------


def test_analyze_macros_forwards_symbolic_literals_to_every_result():
    source = """#define FEAT_1 (0 && A) || B
#define FEAT_2 (0 && A) || (0 && B)
#define BUFFER_SIZE 1024
#define API(x) ((x) && 0)
#define BAD(0)
"""
    plain = analyze_macros(source)
    sym = analyze_macros(source, symbolic_literals=(0,))

    assert [r.semantics for r in plain] == ["ordinary"] * len(plain)
    assert [r.semantics for r in sym] == ["symbolic-literal"] * len(sym)

    assert plain[0].simplified_replacement == "(B)"
    assert sym[0].simplified_replacement is None
    assert plain[1].simplified_replacement == "(0)"
    assert sym[1].simplified_replacement == "(0 && (A || B))"


def test_analyze_macros_rejects_invalid_policy_before_analysis():
    with pytest.raises(AnalysisError) as excinfo:
        analyze_macros("#define FEAT 0 && A\n", symbolic_literals=(1,))
    assert excinfo.value.code == ErrorCode.INVALID_CONFIGURATION
