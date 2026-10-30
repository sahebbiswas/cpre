"""Text reports show a continued or indented directive on one tidy line; JSON keeps the source."""

from __future__ import annotations

import json

import pytest

import cpre
from cpre.cli import main
from cpre.reporting import compact_whitespace

MULTI_LINE = """\
#define FEAT \\
        (A &&        \\
         A   ||      \\
         (A   && B))
#if defined(X) &&    \\
        defined(X)
int x;
#endif
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A  &&   B", "A && B"),
        ("  A\t&&\t\tB  ", "A && B"),
        ("(A &&\n   B)", "(A && B)"),
        ("A&&B", "A&&B"),
        ("", ""),
        ("   ", ""),
        # Literals are shown as written.
        ('X == "a   b"   &&  Y', 'X == "a   b" && Y'),
        ("C == '  '   ||  D", "C == '  ' || D"),
        (r'S == "q\"  \" "   && T', r'S == "q\"  \" " && T'),
        # A digit separator is not a character literal.
        ("1'000   &&   2'000", "1'000 && 2'000"),
        ("x == 'a'   &&   1'0  ||  y", "x == 'a' && 1'0 || y"),
        # An unterminated quote is not a literal; whitespace after it still collapses.
        ('A  "  B', 'A " B'),
    ],
)
def test_compact_whitespace(text, expected):
    assert compact_whitespace(text) == expected


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "multi.c"
    path.write_bytes(MULTI_LINE.encode())
    return path


def test_the_macro_report_shows_the_replaced_line_compacted(source_file, capsys):
    assert main(["simplify-macros", str(source_file)]) == 0
    out = capsys.readouterr().out
    assert "   - (A && A || (A && B))\n" in out
    assert "  " not in out.split("   - ", 1)[1].splitlines()[0]
    assert "   + (!!A)  (proven equivalent)" in out


def test_the_conditional_report_shows_the_directive_compacted(source_file, capsys):
    assert main(["--no-macros", str(source_file)]) == 0
    out = capsys.readouterr().out
    assert "#if defined(X) && defined(X)\n" in out


def test_an_unchanged_macro_is_shown_compacted_with_verbose(tmp_path, capsys):
    path = tmp_path / "ok.c"
    path.write_bytes(
        b"#define KEEP \\\n        (A   &&\n   B)\n#define KEEP2 \\\n    (A   &&   B)\n"
    )
    assert main(["simplify-macros", "--verbose", str(path)]) == 0
    out = capsys.readouterr().out
    assert "#define KEEP2 (A && B)  (expression is already in simplest equivalent form)" in out


def test_json_keeps_the_source_exactly(source_file, capsys):
    assert main(["simplify-macros", "--json", str(source_file)]) == 0
    (record,) = json.loads(capsys.readouterr().out)["macros"]
    assert record["original"] == "(A &&                 A   ||               (A   && B))"


def test_the_api_result_keeps_the_source_exactly():
    (result,) = cpre.analyze_macros(MULTI_LINE)
    assert "          " in result.original_replacement


def test_the_diff_and_rewrite_are_not_compacted(source_file, capsys):
    assert main(["simplify-macros", "--diff", str(source_file)]) == 0
    out = capsys.readouterr().out
    assert "-        (A &&        \\\n" in out
    assert main(["simplify-macros", "--rewrite", str(source_file)]) == 0
    capsys.readouterr()
    rewritten = source_file.read_bytes().decode()
    assert rewritten.startswith("#define FEAT \\\n        (!!A)")
