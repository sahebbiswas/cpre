"""Tests for opt-in macro expansion before Boolean simplification (``expand_macros``, issue #233)."""

import inspect
import itertools
import json

import pytest

import cpre
from cpre import (
    AnalysisError,
    AnalysisOptions,
    ErrorCode,
    MacroConfiguration,
    MacroDefinition,
    analyze_macro,
    analyze_macros,
    evaluate_expression,
    rewrite_macros,
    simplify_macros,
)
from cpre.cli import main

BASIC = """\
#define A (B || C)
#define D (A && C)
"""


def _by_name(results):
    return {result.name: result for result in results}


def _expanded(source, **kwargs):
    return _by_name(analyze_macros(source, expand_macros=True, **kwargs))


# --- The motivating example ------------------------------------------------------------------


def test_expansion_sees_through_a_referenced_macro():
    d = _expanded(BASIC)["D"]
    assert d.simplified is True
    assert d.simplified_replacement == "(!!C)"
    assert d.is_equivalent is True
    assert d.expanded is True
    assert d.expanded_from == ("A",)
    assert d.expanded_replacement == "((B || C) && C)"
    assert d.original_replacement == "(A && C)"


def test_expansion_results_are_reported_never_rewritable():
    d = _expanded(BASIC)["D"]
    assert d.rewritable is False
    assert d.edit is None
    assert d.reason is not None and d.reason.startswith("not rewritten:")
    assert "A" in d.reason


def test_default_is_off_and_output_is_unchanged():
    plain = analyze_macros(BASIC)
    assert plain == analyze_macros(BASIC, expand_macros=False)
    d = _by_name(plain)["D"]
    assert d.simplified is False
    assert d.expanded is False
    assert d.expanded_from == ()
    assert d.expanded_replacement is None
    assert d.expanded_expression is None


def test_a_reference_that_only_inlines_is_not_reported_as_a_simplification():
    results = _expanded("#define A (B && C)\n#define E (A || X)\n")
    e = results["E"]
    assert e.simplified is False
    assert e.expanded_from == ("A",)
    assert e.reason is not None and "after expanding A" in e.reason


def test_macros_without_references_are_unaffected_by_the_option():
    source = "#define FEAT (A || (!A && B))\n#define SIZE 1024\n"
    assert analyze_macros(source, expand_macros=True) == analyze_macros(source)


# --- What is expanded ------------------------------------------------------------------------


def test_expansion_is_transitive_in_expansion_order():
    source = "#define A (B || C)\n#define M (A && A)\n#define D (M && C)\n"
    d = _expanded(source)["D"]
    assert d.simplified_replacement == "(!!C)"
    assert d.expanded_from == ("M", "A")


def test_a_bare_identifier_alias_is_followed():
    source = "#define ALIAS OTHER\n#define G (ALIAS && OTHER)\n"
    g = _expanded(source)["G"]
    assert g.simplified_replacement == "(!!OTHER)"
    assert g.expanded_from == ("ALIAS",)


def test_an_alias_of_a_candidate_is_followed_through():
    source = "#define A (B || C)\n#define A2 A\n#define D (A2 && C)\n"
    d = _expanded(source)["D"]
    assert d.simplified_replacement == "(!!C)"
    assert d.expanded_from == ("A2", "A")


@pytest.mark.parametrize(
    "definition",
    [
        "#define ENABLED 1",  # a plain constant is a configuration-dependent switch
        "#define ENABLED 0",
        "#define ENABLED 2",
        "#define ENABLED (Y + 1)",  # arithmetic
        "#define ENABLED (Y & 1)",  # bitwise
        "#define ENABLED (Y == 1)",  # comparison
        "#define ENABLED(x) (x && C)",  # function-like
        "#define ENABLED",  # empty
    ],
)
def test_non_candidate_macros_stay_opaque_atoms(definition):
    d = _expanded(f"{definition}\n#define D (ENABLED && C)\n")["D"]
    assert d.expanded_from == ()
    assert d.expanded_replacement is None
    assert d.simplified is False


def test_a_constant_switch_is_not_folded_even_when_it_would_simplify():
    source = "#define ENABLED 1\n#define D (ENABLED && C)\n"
    assert _expanded(source)["D"].simplified is False
    assert _expanded(source)["D"].simplified_replacement is None


def test_names_not_defined_in_the_source_stay_atoms():
    d = _expanded("#define D (FROM_A_HEADER && C)\n")["D"]
    assert d.expanded_from == ()
    assert d.complete is True
    assert d.reason == "expression is already in simplest equivalent form"


# --- Soundness guards ------------------------------------------------------------------------


def _reason_for(source):
    d = _expanded(source)["D"]
    assert d.expanded_from == ()
    assert d.simplified is False
    return d.reason


def test_an_unparenthesized_replacement_is_not_expanded_as_a_unit():
    """``#define A B || C`` makes ``A && C`` mean ``B || (C && C)``, not ``(B || C) && C``."""
    source = "#define A B || C\n#define D (A && C)\n"
    d = _expanded(source)["D"]
    assert d.expanded_from == ()
    assert d.simplified is False
    assert "A is not parenthesized" in d.reason
    # The preprocessor's own grouping: D is 1 when B is 1 and C is 0, which expanding A
    # as a unit would have turned into C, that is 0.
    assert _value(source, "D", {"B": 1, "C": 0}) == 1


def test_a_parenthesized_replacement_is_expanded_as_a_unit():
    source = "#define A (B || C)\n#define D (A && C)\n"
    assert _value(source, "D", {"B": 1, "C": 0}) == 0
    assert _expanded(source)["D"].simplified_replacement == "(!!C)"


def test_a_macro_defined_twice_is_not_expanded():
    reason = _reason_for("#define A (B || C)\n#define A (B && C)\n#define D (A && C)\n")
    assert "A is defined more than once" in reason


def test_a_macro_defined_in_exclusive_branches_is_not_expanded():
    source = "#ifdef X\n#define A (B || C)\n#else\n#define A (B && C)\n#endif\n#define D (A && C)\n"
    assert "A is defined more than once" in _reason_for(source)


def test_an_undefined_macro_is_not_expanded():
    reason = _reason_for("#define A (B || C)\n#define D (A && C)\n#undef A\n")
    assert "A is #undef'd" in reason


def test_a_macro_defined_after_its_user_is_not_expanded():
    reason = _reason_for("#define D (A && C)\n#define A (B || C)\n")
    assert "A is defined after D" in reason


def test_a_conditionally_defined_macro_is_not_expanded_outside_its_block():
    reason = _reason_for("#ifdef X\n#define A (B || C)\n#endif\n#define D (A && C)\n")
    assert "A is defined in a conditional block that does not enclose D" in reason


def test_a_macro_in_another_branch_is_not_expanded():
    source = "#if X\n#define A (B || C)\n#else\n#define D (A && C)\n#endif\n"
    assert "does not enclose D" in _reason_for(source)


def test_a_macro_in_the_same_conditional_block_is_expanded():
    source = "#ifdef X\n#define A (B || C)\n#define D (A && C)\n#endif\n"
    assert _expanded(source)["D"].simplified_replacement == "(!!C)"


def test_an_include_guard_around_both_macros_does_not_block_expansion():
    source = "#ifndef G\n#define G\n#define A (B || C)\n#define D (A && C)\n#endif\n"
    assert _expanded(source)["D"].simplified_replacement == "(!!C)"


def test_a_macro_defined_in_an_enclosing_block_is_expanded_into_a_nested_one():
    source = "#ifdef X\n#define A (B || C)\n#if Y\n#define D (A && C)\n#endif\n#endif\n"
    assert _expanded(source)["D"].simplified_replacement == "(!!C)"


def test_a_cycle_leaves_the_atoms_alone():
    results = _expanded("#define A (B && D)\n#define D (A || C)\n")
    assert all(r.complete for r in results.values())
    assert "D is defined after A" in results["A"].reason
    assert results["D"].expanded_from == ("A",)
    assert "D is part of a definition cycle" in results["D"].reason


def test_a_self_referential_macro_is_not_expanded_into_itself():
    a = _expanded("#define A (A && B)\n")["A"]
    assert a.expanded_from == ()
    assert a.reason is not None and "A is part of a definition cycle" in a.reason


def test_a_blocked_reference_does_not_stop_the_others_from_expanding():
    source = "#define A (B || C)\n#define Z (P)\n#define Z (Q)\n#define D ((A && C) && Z)\n"
    d = _expanded(source)["D"]
    assert d.expanded_from == ("A",)
    assert d.simplified_replacement == "(C && Z)"
    assert d.reason is not None and "Z is defined more than once" in d.reason


# --- Semantics and resource limits -----------------------------------------------------------


def test_symbolic_literals_apply_inside_expanded_bodies():
    source = "#define A (0 && B)\n#define D (A || C)\n"
    ordinary = _expanded(source)["D"]
    assert ordinary.simplified_replacement == "(!!C)"
    symbolic = _expanded(source, symbolic_literals=(0,))["D"]
    assert symbolic.semantics == "symbolic-literal"
    assert symbolic.simplified is False
    assert symbolic.expanded_from == ("A",)


WIDE = "#define A (B || C)\n#define E (X || Y)\n#define D (A && E)\n"


def test_expansion_counts_toward_the_atom_limit():
    options = AnalysisOptions(max_atoms=3)
    plain = _by_name(analyze_macros(WIDE, options=options))["D"]
    assert plain.complete is True
    d = _by_name(analyze_macros(WIDE, options=options, expand_macros=True))["D"]
    assert d.complete is False
    assert d.incomplete is not None and d.incomplete.resource == "atoms"
    assert d.simplified_replacement is None and d.edit is None


def test_expansion_counts_toward_the_bdd_node_limit():
    source = (
        "#define A (B1 || C1)\n#define E (B2 && C2)\n#define F (B3 || C3)\n"
        "#define D (A && E && F)\n"
    )
    options = AnalysisOptions(max_bdd_nodes=9)
    assert _by_name(analyze_macros(source, options=options))["D"].complete is True
    d = _by_name(analyze_macros(source, options=options, expand_macros=True))["D"]
    assert d.complete is False
    assert d.incomplete is not None and d.incomplete.resource == "bdd_nodes"
    assert d.simplified_replacement is None and d.edit is None
    assert d.is_equivalent is None


def test_deeply_nested_expansion_is_incomplete_not_a_recursion_error():
    """Each body parses on its own; chaining them multiplies the depth."""
    depth, count = 300, 100
    wrap = lambda inner: "(" + "!(" * depth + inner + ")" * depth + ")"  # noqa: E731
    lines = ["#define M0 (X || Y)"]
    lines += [f"#define M{i} {wrap(f'M{i - 1}')}" for i in range(1, count)]
    lines.append(f"#define TOP (M{count - 1} && W)")
    top = _by_name(
        analyze_macros(
            "\n".join(lines) + "\n",
            options=AnalysisOptions(max_work=10_000_000),
            expand_macros=True,
        )
    )["TOP"]
    assert top.complete is False
    assert top.incomplete is not None and top.incomplete.resource == "nesting"
    assert top.simplified_replacement is None and top.edit is None


def test_exponential_expansion_stops_at_the_work_limit_instead_of_hanging():
    lines = ["#define M0 (X || Y)"]
    lines += [f"#define M{i} (M{i - 1} && M{i - 1})" for i in range(1, 30)]
    lines.append("#define TOP (M29 || Z)")
    results = _by_name(
        analyze_macros(
            "\n".join(lines) + "\n", options=AnalysisOptions(max_work=10_000), expand_macros=True
        )
    )
    top = results["TOP"]
    assert top.complete is False
    assert top.incomplete is not None and top.incomplete.resource == "work"


# --- Soundness oracle ------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    "source",
    [
        BASIC,
        "#define A (B || C)\n#define M (A && A)\n#define D (M && C)\n",
        "#define ALIAS OTHER\n#define D (ALIAS && OTHER)\n",
        "#define A (B || (!B && C))\n#define D ((A && !C) || (A && C))\n",
        "#define A (B && C)\n#define E (!A || B)\n#define D (E && (A || E))\n",
    ],
)
def test_a_reported_replacement_has_the_same_value_as_the_expanded_macro(source):
    """Value, not just truth: ``!!C`` must stay ``!!C`` when ``C`` is 2."""
    d = _expanded(source)["D"]
    assert d.simplified and d.simplified_replacement
    atoms = sorted({"B", "C", "OTHER"} & set(source.replace("(", " ").replace(")", " ").split()))
    for combination in itertools.product((0, 1, 2), repeat=len(atoms)):
        values = dict(zip(atoms, combination, strict=True))
        expected = _value(source, "D", values)
        replaced = source.replace(d.original_replacement, d.simplified_replacement)
        assert _value(replaced, "D", values) == expected, (values, d.simplified_replacement)


# --- API surface -----------------------------------------------------------------------------


def test_simplify_macros_reports_but_never_rewrites_with_expansion():
    result = simplify_macros(BASIC, expand_macros=True)
    assert result.rewritten is False
    assert result.rewritten_source == BASIC
    assert result.applied_count == 0
    assert [r.name for r in result.simplifications] == ["D"]
    assert result.has_findings is True


@pytest.mark.parametrize("rewrite", [True])
def test_simplify_macros_rejects_expansion_together_with_rewrite(rewrite):
    with pytest.raises(AnalysisError) as raised:
        simplify_macros(BASIC, rewrite=rewrite, expand_macros=True)
    assert raised.value.code == ErrorCode.INVALID_CONFIGURATION


def test_rewrite_macros_does_not_take_the_option():
    assert "expand_macros" not in inspect.signature(rewrite_macros).parameters


def test_analyze_macro_takes_the_definitions_to_expand():
    definition = MacroDefinition("D", "(A && C)")
    result = analyze_macro(definition, expand_macros={"A": MacroDefinition("A", "(B || C)")})
    assert result.simplified_replacement == "(!!C)"
    assert result.expanded_from == ("A",)
    assert result.rewritable is False
    assert analyze_macro(definition).simplified is False
    assert analyze_macro(definition, expand_macros={}).simplified is False


def test_analyze_macro_rejects_a_malformed_expansion_mapping():
    with pytest.raises(AnalysisError) as raised:
        analyze_macro(MacroDefinition("D", "(A && C)"), expand_macros=["A"])  # type: ignore[arg-type]
    assert raised.value.code == ErrorCode.INVALID_CONFIGURATION
    with pytest.raises(AnalysisError):
        analyze_macro(
            MacroDefinition("D", "(A && C)"),
            expand_macros={"A": MacroDefinition("OTHER", "(B || C)")},
        )


def test_the_public_namespace_is_unchanged():
    assert cpre.analyze_macros is analyze_macros


# --- Command line ----------------------------------------------------------------------------


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "macros.h"
    path.write_text(BASIC, encoding="utf-8")
    return path


def test_cli_reports_the_expansion(source_file, capsys):
    assert main(["simplify-macros", "--expand-macros", str(source_file)]) == 0
    out = capsys.readouterr().out
    assert "(!!C)" in out
    assert "expanded A" in out and "never rewritten" in out
    assert source_file.read_text(encoding="utf-8") == BASIC


def test_cli_without_the_flag_finds_nothing(source_file, capsys):
    assert main(["simplify-macros", str(source_file)]) == 0
    assert "(!!C)" not in capsys.readouterr().out


def test_cli_json_carries_the_expansion_fields_only_when_asked(source_file, capsys):
    assert main(["simplify-macros", "--expand-macros", "--json", str(source_file)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["expand_macros"] is True
    (record,) = data["macros"]
    assert record["expanded_from"] == ["A"]
    assert record["expanded_replacement"] == "((B || C) && C)"
    assert record["rewritable"] is False

    assert main(["simplify-macros", "--verbose", "--json", str(source_file)]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert "expand_macros" not in plain
    assert all("expanded_from" not in m for m in plain["macros"])


@pytest.mark.parametrize("flag", ["--rewrite", "--in-place", "--diff", "--check"])
def test_cli_rejects_expansion_with_rewrite_modes(source_file, capsys, flag):
    with pytest.raises(SystemExit) as raised:
        main(["simplify-macros", "--expand-macros", flag, str(source_file)])
    assert raised.value.code == 2
    assert "--expand-macros cannot be combined" in capsys.readouterr().err
    assert source_file.read_text(encoding="utf-8") == BASIC


def test_cli_analyze_accepts_the_flag(source_file, capsys):
    assert main(["--expand-macros", "--no-conditionals", str(source_file)]) == 0
    assert "(!!C)" in capsys.readouterr().out


def test_cli_analyze_json_marks_the_run(source_file, capsys):
    assert main(["--expand-macros", "--json", str(source_file)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["expand_macros"] is True
    assert [m["expanded_from"] for m in data["macros"]] == [["A"]]


def test_cli_analyze_rejects_expansion_with_sarif(source_file, capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--expand-macros", "--sarif", str(source_file)])
    assert raised.value.code == 2
    assert "--expand-macros has no effect with --sarif" in capsys.readouterr().err


def test_cli_analyze_rejects_expansion_with_no_macros(source_file, capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--expand-macros", "--no-macros", str(source_file)])
    assert raised.value.code == 2
    assert "--expand-macros has no effect with --no-macros" in capsys.readouterr().err


def test_cli_fail_on_findings_counts_an_expansion_finding(source_file):
    assert main(["simplify-macros", "--expand-macros", "--fail-on-findings", str(source_file)]) == 1


def test_cli_resource_limit_during_expansion_is_an_error(tmp_path, capsys):
    path = tmp_path / "wide.h"
    path.write_text(WIDE, encoding="utf-8")
    assert main(["simplify-macros", "--max-atoms", "3", str(path)]) == 0
    capsys.readouterr()
    assert main(["simplify-macros", "--expand-macros", "--max-atoms", "3", str(path)]) == 2
    assert "atoms" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == WIDE
