from __future__ import annotations

import json
import subprocess
import sys

import pytest

from cpre.cli import main
from cpre.cli import test_input_main as run_test_input


def test_cli_test_input_identity_reduction(capsys):
    """Reduce identity expressions such as A && (A || B) -> A."""
    assert main(["test-input", "A && (A || B)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A && (A || B)\nSimplified: A\n"
    assert captured.err == ""

    assert main(["test-input", "A || (A && B)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A || (A && B)\nSimplified: A\n"


def test_cli_test_input_absorption(capsys):
    """Simplify absorption forms such as (A || B) && A -> A and (!A && B) || A -> A || B."""
    assert main(["test-input", "(A || B) && A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (A || B) && A\nSimplified: A\n"

    assert main(["test-input", "(!A && B) || A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (!A && B) || A\nSimplified: A || B\n"

    assert main(["test-input", "(A && B) || A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (A && B) || A\nSimplified: A\n"


def test_cli_test_input_contradiction(capsys):
    """Reduce contradiction expressions to constant 0."""
    assert main(["test-input", "A && !A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A && !A\nSimplified: 0\n"

    assert main(["test-input", "A && B && !A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A && B && !A\nSimplified: 0\n"


def test_cli_test_input_tautology(capsys):
    """Reduce tautology expressions to constant 1."""
    assert main(["test-input", "A || !A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A || !A\nSimplified: 1\n"

    assert main(["test-input", "A || B || !A"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      A || B || !A\nSimplified: 1\n"


def test_cli_test_input_distributive_and_factoring(capsys):
    """Exercise ROBDD canonicalization on distributive and factoring expressions."""
    assert main(["test-input", "(A && B) || (A && !B)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (A && B) || (A && !B)\nSimplified: A\n"

    assert main(["test-input", "(!A || B) && (A || B)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (!A || B) && (A || B)\nSimplified: B\n"

    assert main(["test-input", "(A && B) || (A && C)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (A && B) || (A && C)\nSimplified: A && (B || C)\n"


def test_cli_test_input_unchanged_expressions(capsys):
    """Preserve expressions that are already in simplest equivalent form."""
    for expr in ["A", "A && B", "A || B", "1", "0"]:
        assert main(["test-input", expr]) == 0
        captured = capsys.readouterr()
        assert captured.out == f"Input:      {expr}\nSimplified: {expr}\n"
        assert captured.err == ""


def test_cli_test_input_nested_and_multiple_atoms(capsys):
    """Simplify nested expressions with multiple Boolean atoms."""
    assert main(["test-input", "((A && B) || (A && !B)) && (C || !C)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      ((A && B) || (A && !B)) && (C || !C)\nSimplified: A\n"

    assert main(["test-input", "!(!A)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      !(!A)\nSimplified: A\n"

    assert main(["test-input", "!(!A && !B)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      !(!A && !B)\nSimplified: A || B\n"


def test_cli_test_input_symbolic_literal_semantics(capsys):
    """Support --symbolic-zero and --symbolic-literal 0 options."""
    # Under ordinary semantics, A && 0 reduces to 0
    assert main(["test-input", "A && 0"]) == 0
    assert "Simplified: 0\n" in capsys.readouterr().out

    # Under symbolic-zero semantics, 0 is treated as a free Boolean atom
    assert main(["test-input", "--symbolic-zero", "(A && 0) || (A && !0)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Input:      (A && 0) || (A && !0)\nSimplified: A\n"

    assert main(["test-input", "--symbolic-literal", "0", "(A && 0) || (A && !0)"]) == 0
    assert "Simplified: A\n" in capsys.readouterr().out


def test_cli_test_input_json_output(capsys):
    """Emit valid machine-readable JSON with documented schema."""
    assert main(["test-input", "--json", "A && (A || B)"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "input": "A && (A || B)",
        "simplified": "A",
        "equivalent": True,
        "changed": True,
        "semantics": "ordinary",
        "symbolic_literals": [],
    }

    # JSON for unchanged expression
    assert main(["test-input", "--json", "A"]) == 0
    data_unchanged = json.loads(capsys.readouterr().out)
    assert data_unchanged == {
        "input": "A",
        "simplified": "A",
        "equivalent": True,
        "changed": False,
        "semantics": "ordinary",
        "symbolic_literals": [],
    }

    # JSON with symbolic zero
    assert main(["test-input", "--json", "--symbolic-zero", "(A && 0) || (A && !0)"]) == 0
    data_symbolic = json.loads(capsys.readouterr().out)
    assert data_symbolic == {
        "input": "(A && 0) || (A && !0)",
        "simplified": "A",
        "equivalent": True,
        "changed": True,
        "semantics": "symbolic-literal",
        "symbolic_literals": [0],
    }


def test_cli_test_input_malformed_syntax_errors(capsys):
    """Reject invalid expressions with stderr diagnostic and exit code 2."""
    cases = [
        ("", "expected a Boolean expression"),
        ("   ", "expected a Boolean expression"),
        ("A &&", "expected an operand after '&&'"),
        ("&& A", "expected an operand"),
        ("A ||", "expected an operand after '||'"),
        ("|| A", "expected an operand"),
        ("!", "expected an operand"),
        ("!()", "expected an operand"),
        ("A && ()", "expected an operand"),
        ("(A && B", "unmatched '('"),
        ("A && B)", "unexpected ')'"),
        ("A && || B", "expected an operand after '&&'"),
        ("A && && B", "expected an operand"),
    ]

    for expr, expected_error in cases:
        code = main(["test-input", expr])
        captured = capsys.readouterr()
        assert code == 2, f"expected code 2 for {expr!r}"
        assert captured.out == ""
        assert "cpre test-input: error:" in captured.err
        assert expected_error in captured.err


def test_cli_test_input_resource_limits(capsys):
    """Report resource-limit exhaustion on stderr with exit code 2."""
    # Exceed atom limit explicitly
    assert main(["test-input", "--max-atoms", "1", "A && B"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "analysis limit exceeded for atoms: 2 > 1" in captured.err

    # Exceed work limit explicitly
    assert main(["test-input", "--max-work", "1", "A && B"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "analysis limit exceeded for work:" in captured.err

    # Exceed default max_atoms (64) with 65 atoms
    large_expr = " || ".join(f"ATOM_{i}" for i in range(65))
    assert main(["test-input", large_expr]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "analysis limit exceeded for atoms: 65 > 64" in captured.err


def test_cli_test_input_option_validation(capsys):
    """Reject invalid option values with parser error."""
    with pytest.raises(SystemExit) as exc:
        main(["test-input", "--max-atoms", "0", "A"])
    assert exc.value.code == 2
    assert "max_atoms must be a positive integer" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        main(["test-input", "--max-work", "-5", "A"])
    assert exc.value.code == 2
    assert "max_work must be a positive integer" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        main(["test-input", "--symbolic-literal", "1", "A"])
    assert exc.value.code == 2
    assert "unsupported symbolic literal 1" in capsys.readouterr().err


def test_cli_test_input_missing_expression_argument(capsys):
    """Reject missing expression argument with usage error."""
    with pytest.raises(SystemExit) as exc:
        main(["test-input"])
    assert exc.value.code == 2
    assert "the following arguments are required: expression" in capsys.readouterr().err


def test_cli_test_input_help(capsys):
    """Display help message for test-input command."""
    with pytest.raises(SystemExit) as exc:
        run_test_input(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Parse and simplify an arbitrary Boolean expression using ROBDD." in captured.out
    assert "--json" in captured.out
    assert "--symbolic-zero" in captured.out


def test_cli_test_input_module_entrypoint():
    """Verify test-input works via python -m cpre."""
    proc = subprocess.run(
        [sys.executable, "-m", "cpre", "test-input", "A && (A || B)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == "Input:      A && (A || B)\nSimplified: A\n"
    assert proc.stderr == ""
