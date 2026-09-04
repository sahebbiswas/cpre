import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "cpre" / "cpre.py"

from cpre import cpre as conditions


def branch(tree, group=0, index=0):
    return tree.groups[group].branches[index]


def test_requested_absorption_example_simplifies_to_a():
    expression = conditions.parse_expression("(A && B && (C || !D)) || A")

    assert conditions.format_expression(conditions.simplify(expression)) == "A"


def test_complements_and_constants_are_simplified():
    expression = conditions.parse_expression("(A && !A) || (B && 1) || 0")

    assert conditions.format_expression(conditions.simplify(expression)) == "B"


def test_exact_reported_simplification_handles_consensus_identity():
    tree = conditions.analyze_source("#if (A && B) || (A && !B)\n#endif\n")

    assert conditions.format_expression(branch(tree).analysis.simplified) == "A"


def test_bdd_variable_order_follows_first_appearance():
    expression = conditions.parse_expression("Z && A && Z && M")
    atoms = conditions._expression_atoms_in_order(expression)

    bdd = conditions._BDD(atoms)

    assert bdd.atoms == [
        conditions.Variable("Z"),
        conditions.Variable("A"),
        conditions.Variable("M"),
    ]


def test_first_appearance_order_limits_synthetic_bdd_size():
    # Pairwise equivalence has a linear ROBDD when each pair is adjacent, but
    # the former alphabetical order grouped all uppercase and lowercase flags
    # and produced 3,069 reachable nodes for ten pairs.
    clauses = [
        f"(({upper} && {lower}) || (!{upper} && !{lower}))"
        for upper, lower in zip("ABCDEFGHIJ", "abcdefghij")
    ]
    expression = conditions.parse_expression(" && ".join(clauses))
    bdd = conditions._BDD(conditions._expression_atoms_in_order(expression))

    root = bdd.build(expression)

    assert bdd.node_count(root) == 30


def test_elif_shadowed_by_broader_if_is_dead():
    tree = conditions.analyze_source(
        """
#if (A || B)
ignored();
#elif A
also_ignored();
#endif
"""
    )

    assert branch(tree, index=0).analysis.status == "reachable"
    assert branch(tree, index=1).analysis.status == "dead"
    assert "contradicts" in branch(tree, index=1).analysis.reason


def test_nested_repeated_condition_is_redundant():
    tree = conditions.analyze_source(
        """
#if A
#if A || B
#endif
#endif
"""
    )

    nested = branch(tree).children[0].branches[0]
    assert nested.analysis.status == "redundant"
    assert conditions.format_expression(nested.analysis.contextual) == "1"


def test_nested_condition_is_decayed_using_parent_context():
    tree = conditions.analyze_source(
        """
#if A
#if A && (B || !A)
#endif
#endif
"""
    )

    nested = branch(tree).children[0].branches[0]
    assert nested.analysis.status == "reachable"
    assert conditions.format_expression(nested.analysis.contextual) == "B"


def test_else_is_dead_after_exhaustive_conditions():
    tree = conditions.analyze_source(
        """
#if A
#elif !A
#else
#endif
"""
    )

    assert branch(tree, index=2).analysis.status == "dead"
    assert "cover every remaining case" in branch(tree, index=2).analysis.reason


def test_dead_parent_makes_nested_branch_dead():
    tree = conditions.analyze_source(
        """
#if 0
#if A
#endif
#endif
"""
    )

    nested = branch(tree).children[0].branches[0]
    assert nested.analysis.status == "dead"
    assert nested.analysis.reason == "enclosing branch is unreachable"


def test_ifdef_ifndef_and_defined_forms():
    tree = conditions.analyze_source(
        """
#ifdef FEATURE
#elif defined(FALLBACK) && !defined DISABLED
#endif
#ifndef OTHER
#endif
"""
    )

    assert conditions.format_expression(branch(tree).expression) == "FEATURE"
    assert (
        conditions.format_expression(branch(tree, index=1).expression)
        == "FALLBACK && !DISABLED"
    )
    assert conditions.format_expression(branch(tree, group=1).expression) == "!OTHER"


def test_c23_elifdef_and_elifndef_forms():
    tree = conditions.analyze_source(
        """
#if PRIMARY
#elifdef FALLBACK
#elifndef DISABLED
#endif
"""
    )

    branches = tree.groups[0].branches
    assert [item.directive for item in branches] == ["if", "elifdef", "elifndef"]
    assert branches[1].expression == conditions.Variable("FALLBACK")
    assert branches[2].expression == conditions.Negation(
        conditions.Variable("DISABLED")
    )


def test_value_expression_becomes_opaque_predicate_without_losing_boolean_shape():
    expression = conditions.parse_expression("VERSION >= 4 && defined(FOO)")

    assert isinstance(expression, conditions.Conjunction)
    assert conditions.Predicate("VERSION >= 4") in expression.operands
    assert conditions.Variable("FOO") in expression.operands


def test_parent_flag_reasoning_continues_with_opaque_predicate():
    tree = conditions.analyze_source(
        """
#if VERSION >= 4 && defined(FOO)
#if defined(FOO)
#endif
#endif
"""
    )

    outer = branch(tree)
    nested = outer.children[0].branches[0]
    assert nested.analysis.status == "redundant"
    assert conditions.expression_predicates(outer.expression) == {"VERSION >= 4"}


def test_equivalent_repeated_opaque_predicate_makes_elif_dead():
    tree = conditions.analyze_source(
        """
#if VERSION >= 4 && FOO
#elif FOO && VERSION >= 4
#endif
"""
    )

    assert branch(tree, index=1).analysis.status == "dead"


def test_comparison_not_equal_is_not_parsed_as_boolean_negation():
    expression = conditions.parse_expression("VERSION != 4 && !DISABLED")

    assert isinstance(expression, conditions.Conjunction)
    assert conditions.Predicate("VERSION != 4") in expression.operands
    assert conditions.Negation(conditions.Variable("DISABLED")) in expression.operands


def test_boolean_negation_of_parenthesized_predicate_is_preserved():
    expression = conditions.parse_expression("!(VERSION >= 4)")

    assert expression == conditions.Negation(conditions.Predicate("VERSION >= 4"))
    assert conditions.format_expression(expression) == "!(VERSION >= 4)"


def test_unparenthesized_value_negation_remains_inside_opaque_predicate():
    expression = conditions.parse_expression("!VERSION >= 4")

    assert expression == conditions.Predicate("! VERSION >= 4")


def test_logical_text_inside_function_argument_remains_opaque():
    expression = conditions.parse_expression(
        '__has_include("platform( && )config.h") && FEATURE'
    )

    assert isinstance(expression, conditions.Conjunction)
    assert (
        conditions.Predicate('__has_include("platform( && )config.h")')
        in expression.operands
    )


def test_multiline_directive_and_comments_preserve_start_line():
    tree = conditions.analyze_source(
        """// heading
#if A && \\
    (B || B) /* duplicate */
#endif
"""
    )

    assert branch(tree).line == 2
    assert conditions.format_expression(branch(tree).analysis.simplified) == "A && B"


def test_multiline_directive_error_reports_physical_line_and_column():
    source = "#if A && \\\n    B)\n#endif\n"

    with pytest.raises(
        conditions.ExpressionSyntaxError,
        match=r"unexpected '\)' at line 2, column 6",
    ) as error:
        conditions.analyze_source(source)

    assert error.value.location == conditions._SourceLocation(line=2, column=6)


def test_empty_directive_expression_has_structured_location():
    with pytest.raises(conditions.ExpressionSyntaxError) as error:
        conditions.analyze_source("#if\n#endif\n")

    assert error.value.code == "expression_syntax"
    assert error.value.location == conditions._SourceLocation(line=1, column=1)
    assert str(error.value) == "expected a Boolean expression at line 1, column 1"


@pytest.mark.parametrize(
    "source,message",
    [
        ("#elif A\n", "no matching #if"),
        ("#if A\n#else\n#else\n#endif\n", "duplicate #else"),
        ("#if A\n", "no matching #endif"),
        ("#if A &&\n#endif\n", "expected an operand"),
        ("#if (A || B\n#endif\n", r"expected.*'\)'"),
        ("#if 08\n#endif\n", "invalid integer"),
        ("#if A\n#elifdef B C\n#endif\n", "expects exactly one macro name"),
    ],
)
def test_malformed_input_has_a_clear_diagnostic(source, message):
    with pytest.raises(conditions.ConditionError, match=message):
        conditions.analyze_source(source)


def test_json_tree_preserves_nesting():
    tree = conditions.analyze_source("#if A\n#if B\n#endif\n#endif\n")

    result = conditions.tree_to_dict(tree)
    nested = result["groups"][0]["branches"][0]["children"][0]
    assert result["groups"][0]["end_line"] == 4
    assert nested["end_line"] == 3
    assert nested["branches"][0]["condition"] == "B"


def test_json_identifies_opaque_predicates():
    tree = conditions.analyze_source("#if (X + 1) >= LIMIT\n#endif\n")

    result = conditions.tree_to_dict(tree)
    assert result["groups"][0]["branches"][0]["opaque_predicates"] == [
        "(X + 1) >= LIMIT"
    ]


def test_text_report_labels_opaque_predicates():
    tree = conditions.analyze_source("#if VERSION >= 4 && FOO\n#endif\n")

    assert "opaque: VERSION >= 4" in conditions.format_report(tree)


def test_default_report_filters_unchanged_branches():
    tree = conditions.analyze_source(
        """#if UNCHANGED
#endif
#if DUP && DUP
#endif
#if 0
#endif
#if FLAG || !FLAG
#endif
"""
    )

    report = conditions.format_report(tree, verbose=False)

    assert "#if UNCHANGED" not in report
    assert "#if DUP && DUP [reachable]" in report
    assert "#if 0 [dead]" in report
    assert "#if FLAG || !FLAG [redundant]" in report


def test_filtered_json_retains_unchanged_ancestor_of_changed_branch():
    tree = conditions.analyze_source(
        """#if PARENT
#if CHILD && CHILD
#endif
#endif
"""
    )

    result = conditions.tree_to_dict(tree, verbose=False)

    parent = result["groups"][0]["branches"][0]
    nested = parent["children"][0]["branches"][0]
    assert parent["condition"] == "PARENT"
    assert "simplified_condition" not in parent
    assert "contextual_condition" not in parent
    assert "effective_condition" not in parent
    assert nested["condition"] == "CHILD && CHILD"
    assert nested["simplified_condition"] == "CHILD"


def test_filtered_text_uses_header_only_for_unchanged_ancestor():
    tree = conditions.analyze_source(
        """#if PARENT
#if CHILD && CHILD
#endif
#endif
"""
    )

    lines = conditions.format_report(tree, verbose=False).splitlines()

    assert lines[0] == "1: #if PARENT [reachable]"
    assert lines[1] == "  2: #if CHILD && CHILD [reachable]"


def test_verbose_json_keeps_details_for_unchanged_ancestor():
    tree = conditions.analyze_source(
        """#if PARENT
#if CHILD && CHILD
#endif
#endif
"""
    )

    parent = conditions.tree_to_dict(tree, verbose=True)["groups"][0]["branches"][0]

    assert parent["simplified_condition"] == "PARENT"
    assert parent["contextual_condition"] == "PARENT"
    assert parent["effective_condition"] == "PARENT"


def test_default_report_includes_context_only_simplification():
    tree = conditions.analyze_source(
        """#if PARENT
#if PARENT && CHILD
#endif
#endif
"""
    )

    report = conditions.format_report(tree, verbose=False)

    assert "#if PARENT && CHILD [reachable]" in report
    assert "in context: CHILD" in report


def test_formatting_only_normalization_is_not_reported_as_changed():
    tree = conditions.analyze_source(
        """#if (PARENTHESIZED)
#endif
#if defined(FEATURE)
#endif
"""
    )

    report = conditions.format_report(tree, verbose=False)
    result = conditions.tree_to_dict(tree, verbose=False)

    assert report == "No changed, dead, or redundant conditional directives found."
    assert result == {"groups": []}


def test_operand_order_and_association_are_not_reported_as_changes():
    tree = conditions.analyze_source(
        """#if B && A
#endif
#if C || (A || B)
#endif
"""
    )

    report = conditions.format_report(tree, verbose=False)
    result = conditions.tree_to_dict(tree, verbose=False)

    assert report == "No changed, dead, or redundant conditional directives found."
    assert result == {"groups": []}


def test_visibility_is_computed_once_per_branch(monkeypatch):
    tree = conditions.analyze_source(
        """#if OUTER
#if MIDDLE
#if INNER && INNER
#endif
#endif
#endif
"""
    )
    original = conditions._branch_is_notable
    calls = {}

    def counted(branch):
        calls[id(branch)] = calls.get(id(branch), 0) + 1
        return original(branch)

    monkeypatch.setattr(conditions, "_branch_is_notable", counted)

    conditions.format_report(tree, verbose=False)

    assert sorted(calls.values()) == [1, 1, 1]


def test_colored_report_marks_branch_categories():
    tree = conditions.analyze_source(
        """#if UNCHANGED
#endif
#if DUP && DUP
#endif
#if 0
#endif
#if FLAG || !FLAG
#endif
"""
    )

    report = conditions.format_report(tree, verbose=True, color=True)

    assert "\033[90m1: #if UNCHANGED [reachable]\033[0m" in report
    assert "\033[32m3: #if DUP && DUP [reachable]\033[0m" in report
    assert "\033[31m5: #if 0 [dead]\033[0m" in report
    assert "\033[33m7: #if FLAG || !FLAG [redundant]\033[0m" in report


def test_cli_json_and_fail_on_findings(tmp_path):
    source = tmp_path / "sample.c"
    source.write_text("#if A || B\n#elif A\n#endif\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--json", "--fail-on-findings"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["groups"][0]["branches"][0]["status"] == "dead"


def test_cli_reports_non_utf8_input_without_traceback(tmp_path):
    source = tmp_path / "invalid.c"
    source.write_bytes(b"#if A\n\xff\n#endif\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert str(source) in result.stderr
    assert "codec can't decode byte" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_recursively_analyzes_c_and_cpp_sources(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    first = tmp_path / "first.c"
    second = nested / "second.hpp"
    ignored = nested / "notes.txt"
    first.write_text("#if A\n#endif\n", encoding="utf-8")
    second.write_text("#if B\n#endif\n", encoding="utf-8")
    ignored.write_text("#if IGNORED\n#endif\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--recursive",
            str(tmp_path),
            "--json",
            "--verbose",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert [Path(item["path"]).name for item in payload["files"]] == [
        "first.c",
        "second.hpp",
    ]
    assert [
        item["groups"][0]["branches"][0]["condition"]
        for item in payload["files"]
    ] == ["A", "B"]


def test_cli_recursive_fail_on_findings_aggregates_files(tmp_path):
    (tmp_path / "clean.c").write_text("#if A\n#endif\n", encoding="utf-8")
    (tmp_path / "finding.h").write_text("#if 0\n#endif\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--recursive",
            str(tmp_path),
            "--fail-on-findings",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert f"== {tmp_path / 'clean.c'} ==" not in result.stdout
    assert f"== {tmp_path / 'finding.h'} ==" in result.stdout


def test_cli_requires_recursive_for_directory_input(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "use --recursive" in result.stderr


def test_cli_batch_continues_after_malformed_file(tmp_path):
    malformed = tmp_path / "malformed.c"
    valid = tmp_path / "valid.c"
    malformed.write_text("#if\n#endif\n", encoding="utf-8")
    valid.write_text("#if VALID\n#endif\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(malformed),
            str(valid),
            "--json",
            "--verbose",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert str(malformed) in result.stderr
    assert [item["path"] for item in payload["files"]] == [str(valid)]


def test_cli_single_file_json_error_does_not_emit_batch_schema(tmp_path):
    malformed = tmp_path / "malformed.c"
    malformed.write_text("#if\n#endif\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(malformed), "--json"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert str(malformed) in result.stderr


def test_cli_json_filters_by_default_and_verbose_restores_full_tree(tmp_path):
    source = tmp_path / "conditions.c"
    source.write_text(
        """#if UNCHANGED
#endif
#if DUP && DUP
#endif
#if 0
#endif
""",
        encoding="utf-8",
    )

    concise = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--json"],
        capture_output=True,
        check=False,
        text=True,
    )
    verbose = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--json", "--verbose"],
        capture_output=True,
        check=False,
        text=True,
    )

    concise_conditions = [
        group["branches"][0]["condition"]
        for group in json.loads(concise.stdout)["groups"]
    ]
    verbose_conditions = [
        group["branches"][0]["condition"]
        for group in json.loads(verbose.stdout)["groups"]
    ]
    assert concise.returncode == verbose.returncode == 0
    assert concise_conditions == ["DUP && DUP", "0"]
    assert verbose_conditions == ["UNCHANGED", "DUP && DUP", "0"]
    assert "\033[" not in concise.stdout
    assert "\033[" not in verbose.stdout


def test_cli_batch_json_omits_files_without_displayed_entries(tmp_path):
    (tmp_path / "unchanged.c").write_text("#if OK\n#endif\n", encoding="utf-8")
    (tmp_path / "changed.c").write_text(
        "#if DUP && DUP\n#endif\n", encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--recursive", str(tmp_path), "--json"],
        capture_output=True,
        check=False,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert [Path(item["path"]).name for item in payload["files"]] == ["changed.c"]
