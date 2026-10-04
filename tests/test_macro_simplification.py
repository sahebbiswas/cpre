"""Tests for bounded ROBDD Boolean simplification of object-like macros."""

import pytest

import cpre
from cpre import (
    AnalysisIncomplete,
    AnalysisOptions,
    ErrorCode,
    MacroAnalysisResult,
    MacroDefinition,
    SourceLocation,
    analyze_macro,
    analyze_macros,
)


def test_issue_examples_candidates():
    # FEAT_1 ( A || ( ! A && B ) ) -> ( A || B )
    d1 = MacroDefinition("FEAT_1", "(A || (!A && B))")
    r1 = analyze_macro(d1)
    assert r1.candidate is True
    assert r1.simplified is True
    assert r1.simplified_replacement == "(A || B)"
    assert r1.is_equivalent is True
    assert r1.incomplete is None

    # Spaced original replacement: ( A || ( ! A && B ) )
    d1_spaced = MacroDefinition("FEAT_1", "( A || ( ! A && B ) )")
    r1_spaced = analyze_macro(d1_spaced)
    assert r1_spaced.candidate is True
    assert r1_spaced.simplified is True
    assert r1_spaced.simplified_replacement == "(A || B)"
    assert r1_spaced.is_equivalent is True

    # FEAT_2 ((A && B) || (A && !B)) -> (A)
    d2 = MacroDefinition("FEAT_2", "((A && B) || (A && !B))")
    r2 = analyze_macro(d2)
    assert r2.candidate is True
    assert r2.simplified is True
    assert r2.simplified_replacement == "(A)"
    assert r2.is_equivalent is True

    # FEAT_3 (!!A) -> (A)
    d3 = MacroDefinition("FEAT_3", "(!!A)")
    r3 = analyze_macro(d3)
    assert r3.candidate is True
    assert r3.simplified is True
    assert r3.simplified_replacement == "(A)"
    assert r3.is_equivalent is True


def test_issue_examples_non_candidates():
    # BUFFER_SIZE 1024
    r_buf = analyze_macro(MacroDefinition("BUFFER_SIZE", "1024"))
    assert r_buf.candidate is False
    assert r_buf.simplified is False
    assert r_buf.simplified_replacement is None
    assert "non-Boolean integer literal" in r_buf.reason

    # MAGIC 0x8000
    r_magic = analyze_macro(MacroDefinition("MAGIC", "0x8000"))
    assert r_magic.candidate is False
    assert "non-Boolean integer literal" in r_magic.reason

    # MESSAGE "hello"
    r_msg = analyze_macro(MacroDefinition("MESSAGE", '"hello"'))
    assert r_msg.candidate is False
    assert "string literal" in r_msg.reason

    # MASK (1 << 7)
    r_mask = analyze_macro(MacroDefinition("MASK", "(1 << 7)"))
    assert r_mask.candidate is False
    assert "unsupported operator" in r_mask.reason

    # API(x) ((x) + 1)
    r_api = analyze_macro(MacroDefinition("API", "((x) + 1)", parameters=("x",)))
    assert r_api.candidate is False
    assert "function-like" in r_api.reason


def test_more_non_candidates():
    # Empty replacement
    r_empty = analyze_macro(MacroDefinition("EMPTY", ""))
    assert r_empty.candidate is False
    assert "empty" in r_empty.reason

    r_spaces = analyze_macro(MacroDefinition("BLANK", "   "))
    assert r_spaces.candidate is False
    assert "empty" in r_spaces.reason

    # Bare identifiers without Boolean operators
    r_alias = analyze_macro(MacroDefinition("ALIAS", "OTHER"))
    assert r_alias.candidate is False
    assert "no Boolean operators" in r_alias.reason

    r_wrapped_id = analyze_macro(MacroDefinition("WRAPPED_ID", "(OTHER)"))
    assert r_wrapped_id.candidate is False
    assert "no Boolean operators" in r_wrapped_id.reason

    # Integer constants 0 and 1 without Boolean operators
    r_zero = analyze_macro(MacroDefinition("ZERO", "0"))
    assert r_zero.candidate is False
    assert "no Boolean operators" in r_zero.reason

    r_one = analyze_macro(MacroDefinition("ONE", "1"))
    assert r_one.candidate is False
    assert "no Boolean operators" in r_one.reason

    r_zero_paren = analyze_macro(MacroDefinition("ZERO", "(0)"))
    assert r_zero_paren.candidate is False
    assert "no Boolean operators" in r_zero_paren.reason

    # Function calls
    r_call = analyze_macro(MacroDefinition("CALL", "foo()"))
    assert r_call.candidate is False
    assert "function calls" in r_call.reason

    r_call_args = analyze_macro(MacroDefinition("CALL", "foo(A, B)"))
    assert r_call_args.candidate is False
    assert "function calls" in r_call_args.reason

    # Unsupported C operators
    for text in ["(A + B)", "(A - B)", "(A * B)", "(A / B)", "(A % B)"]:
        r = analyze_macro(MacroDefinition("ARITH", text))
        assert r.candidate is False
        assert "unsupported" in r.reason

    for text in ["(A & B)", "(A | B)", "(A ^ B)", "(~A)", "(A >> 2)"]:
        r = analyze_macro(MacroDefinition("BITWISE", text))
        assert r.candidate is False
        assert "unsupported" in r.reason

    for text in ["(A == B)", "(A != B)", "(A < B)", "(A <= B)", "(A > B)", "(A >= B)"]:
        r = analyze_macro(MacroDefinition("COMP", text))
        assert r.candidate is False
        assert "unsupported" in r.reason

    r_ternary = analyze_macro(MacroDefinition("COND", "(A ? B : C)"))
    assert r_ternary.candidate is False
    assert "unsupported" in r_ternary.reason

    r_comma = analyze_macro(MacroDefinition("COMMA", "(A, B)"))
    assert r_comma.candidate is False
    assert "unsupported" in r_comma.reason

    # 'defined' in macro replacement list is not supported
    r_def = analyze_macro(MacroDefinition("DEF", "defined(A)"))
    assert r_def.candidate is False
    assert "defined" in r_def.reason

    # Malformed syntax
    r_malformed = analyze_macro(MacroDefinition("ERR", "(A &&)"))
    assert r_malformed.candidate is False
    assert "malformed" in r_malformed.reason

    r_unmatched = analyze_macro(MacroDefinition("ERR", "(A || B"))
    assert r_unmatched.candidate is False
    assert "unmatched" in r_unmatched.reason


def test_boolean_identities_and_simplifications():
    cases = [
        ("(A && 1)", "(A)"),
        ("(1 && A)", "(A)"),
        ("(0 || B)", "(B)"),
        ("(B || 0)", "(B)"),
        ("(0 && A)", "(0)"),
        ("(A && 0)", "(0)"),
        ("(1 || A)", "(1)"),
        ("(A || 1)", "(1)"),
        ("(A && !A)", "(0)"),
        ("(A || !A)", "(1)"),
        ("(A && (A || B))", "(A)"),
        ("(A || (A && B))", "(A)"),
        ("((!A && !B) || (!A && B))", "(!A)"),
        ("(A && B && A)", "(A && B)"),
        ("(A || B || A)", "(A || B)"),
        ("(!0)", "(1)"),
        ("(!1)", "(0)"),
        ("(!(!A))", "(A)"),
        ("((A && B) || (A && !B && C) || (A && !B && !C))", "(A)"),
    ]

    for original, expected in cases:
        r = analyze_macro(MacroDefinition("TEST", original))
        assert r.candidate is True, f"Expected candidate for {original}"
        assert r.simplified is True, f"Expected simplified for {original}"
        assert r.simplified_replacement == expected, (
            f"For {original}: got {r.simplified_replacement}, expected {expected}"
        )
        assert r.is_equivalent is True


def test_unwrapped_replacements():
    # If the original macro replacement is not parenthesized, outer parens are not forced
    d1 = MacroDefinition("FEAT", "A || (!A && B)")
    r1 = analyze_macro(d1)
    assert r1.candidate is True
    assert r1.simplified is True
    assert r1.simplified_replacement == "A || B"
    assert r1.is_equivalent is True

    d2 = MacroDefinition("FEAT", "!!A")
    r2 = analyze_macro(d2)
    assert r2.candidate is True
    assert r2.simplified is True
    assert r2.simplified_replacement == "A"
    assert r2.is_equivalent is True

    d3 = MacroDefinition("FEAT", "!(!(!A))")
    r3 = analyze_macro(d3)
    assert r3.candidate is True
    assert r3.simplified is True
    assert r3.simplified_replacement == "!A"
    assert r3.is_equivalent is True


def test_already_simplest_form():
    for text in ["(A || B)", "(A && B)", "(!A)", "A || B", "A && B", "!A"]:
        r = analyze_macro(MacroDefinition("TEST", text))
        assert r.candidate is True
        assert r.simplified is False
        assert r.simplified_replacement is None
        assert r.is_equivalent is True
        assert "simplest equivalent form" in r.reason


def test_semantic_boundary_no_expansion():
    # Identifiers in replacement lists are symbolic Boolean atoms, never recursively expanded
    source = """
#define A 1
#define B 0
#define FEAT (A || B)
"""
    results = analyze_macros(source)
    assert len(results) == 3

    assert results[0].name == "A"
    assert results[0].candidate is False  # 1 is an integer literal

    assert results[1].name == "B"
    assert results[1].candidate is False  # 0 is an integer literal

    assert results[2].name == "FEAT"
    assert results[2].candidate is True
    # A and B are treated as symbolic variables; (A || B) is not evaluated to (1 || 0) = 1
    assert results[2].simplified is False
    assert results[2].simplified_replacement is None
    assert results[2].is_equivalent is True


def test_deterministic_robdd_resource_limits():
    # Expression with 3 atoms: A, B, C
    definition = MacroDefinition("BIG", "(A || B || C || (A && !B))")

    # Exceed max_atoms
    limited_atoms = AnalysisOptions(max_atoms=2)
    r_atoms = analyze_macro(definition, options=limited_atoms)
    assert r_atoms.candidate is True
    assert r_atoms.complete is False
    assert r_atoms.incomplete is not None
    assert r_atoms.incomplete.code == ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert r_atoms.incomplete.resource == "atoms"
    assert r_atoms.simplified is False
    assert r_atoms.simplified_replacement is None
    assert r_atoms.is_equivalent is None

    # Exceed max_work
    limited_work = AnalysisOptions(max_work=5)
    r_work = analyze_macro(definition, options=limited_work)
    assert r_work.candidate is True
    assert r_work.complete is False
    assert r_work.incomplete is not None
    assert r_work.incomplete.code == ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert r_work.incomplete.resource == "work"
    assert r_work.simplified is False
    assert r_work.simplified_replacement is None
    assert r_work.is_equivalent is None


def test_analyze_macros_from_source_with_provenance_and_ranges():
    source = """#define FEAT_1 (A || (!A && B))
#define BUFFER_SIZE 1024
#define FEAT_2 ((A && B) || (A && !B))
#define API(x) ((x) + 1)
#define FEAT_3 (!!A)
"""
    results = analyze_macros(source)
    assert len(results) == 5

    r0, r1, r2, r3, r4 = results

    assert r0.name == "FEAT_1"
    assert r0.candidate is True
    assert r0.simplified is True
    assert r0.simplified_replacement == "(A || B)"
    assert r0.location == SourceLocation(1, 9)
    assert r0.replacement_range is not None

    # Verify replacement_range points precisely to "(A || (!A && B))"
    lines = source.splitlines(keepends=True)
    start_offset = sum(len(lines[i]) for i in range(r0.replacement_range.start.line - 1)) + (
        r0.replacement_range.start.column - 1
    )
    end_offset = sum(len(lines[i]) for i in range(r0.replacement_range.end.line - 1)) + (
        r0.replacement_range.end.column - 1
    )
    assert source[start_offset:end_offset] == "(A || (!A && B))"

    # Rewriting this range produces the simplified macro line!
    rewritten_source = source[:start_offset] + r0.simplified_replacement + source[end_offset:]
    assert "#define FEAT_1 (A || B)\n" in rewritten_source

    assert r1.name == "BUFFER_SIZE"
    assert r1.candidate is False

    assert r2.name == "FEAT_2"
    assert r2.candidate is True
    assert r2.simplified is True
    assert r2.simplified_replacement == "(A)"

    assert r3.name == "API"
    assert r3.candidate is False

    assert r4.name == "FEAT_3"
    assert r4.candidate is True
    assert r4.simplified is True
    assert r4.simplified_replacement == "(A)"


def test_multiline_and_commented_macros():
    source = """#define FEAT_MULTI \\\n    (A || \\\n     (!A && B)) /* trailing comment */\n"""
    results = analyze_macros(source)
    assert len(results) == 1
    r = results[0]
    assert r.name == "FEAT_MULTI"
    assert r.candidate is True
    assert r.simplified is True
    assert r.simplified_replacement == "(A || B)"
    assert r.is_equivalent is True


def test_existing_analyze_source_is_unaffected():
    # Existing preprocessor conditional control analysis must remain 100% unchanged
    source = """#if FEATURE
int x = 1;
#elif FEATURE
int x = 2;
#endif
"""
    result = cpre.analyze_source(source)
    assert len(result.findings) == 1
    assert result.findings[0].kind == cpre.FindingKind.DEAD_BRANCH
    assert result.findings[0].directive == "elif"
