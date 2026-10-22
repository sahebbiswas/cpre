"""Every component shares one physical line model (issue #140).

A line ends at ``\\r\\n``, a lone ``\\r`` or ``\\n``, as in GCC and Clang. Form feed,
vertical tab and the other separators ``str.splitlines`` honours stay inside a line.
"""

import pytest

import cpre
from cpre._lines import line_starts, split_lines
from cpre.cli import main

LINE_BREAKS = ["\n", "\r\n", "\r"]
# Characters str.splitlines() treats as line boundaries but C compilers do not.
IN_LINE = ["\f", "\v", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.mark.parametrize(
    "text",
    ["", "a", "a\n", "a\r\nb", "a\rb\r", "\n\n", "a\r\r\nb", "a\fb\vc\x85d e\n"],
)
def test_split_lines_matches_splitlines_on_line_breaks_only(text):
    plain = text
    for separator in IN_LINE:
        plain = plain.replace(separator, "x")
    assert split_lines(plain) == plain.splitlines()
    assert split_lines(plain, keepends=True) == plain.splitlines(keepends=True)
    assert "".join(split_lines(text, keepends=True)) == text


def test_split_lines_keeps_in_line_separators():
    assert split_lines("a\fb\vc\x1cd\x85e f g\nh") == ["a\fb\vc\x1cd\x85e f g", "h"]


def test_line_starts():
    assert line_starts("") == [0]
    assert line_starts("a\r\nb\rc\nd\fe") == [0, 3, 5, 7]
    assert line_starts("a\n") == [0, 2]


def _source(prefix, newline):
    return newline.join(
        [f"int a;{prefix}", "#if A || A", "#define W (X || X)", "#endif", "int b;", ""]
    )


CASES = [(prefix, newline) for newline in LINE_BREAKS for prefix in ["", *IN_LINE]]


@pytest.mark.parametrize(("prefix", "newline"), CASES)
def test_every_component_reports_compiler_line_numbers(prefix, newline):
    source = _source(prefix, newline)

    findings = cpre.analyze_source(source).findings
    assert [finding.location.line for finding in findings] == [2]

    tree = cpre.parse_conditionals(source)
    assert [directive.source_range.start.line for directive in tree.directives] == [2, 4]
    assert not tree.diagnostics

    (macro,) = cpre.simplify_macros(source).results
    assert macro.location.line == 3

    assert cpre.rewrite_macros(source) == source.replace("(X || X)", "(!!X)")

    result = cpre.preprocess_source(
        source, configuration=cpre.MacroConfiguration(integers={"A": 0}, unknown_names="undefined")
    )
    assert result.removed_lines == frozenset({2, 3, 4})
    assert split_lines(result.source)[4] == "int b;"


@pytest.mark.parametrize("separator", IN_LINE)
def test_in_line_separator_does_not_start_a_directive(separator):
    source = f"int a;{separator}#define X 1\nint b = X;\n"

    result = cpre.preprocess_source(source)

    assert "int b = X;" in result.source
    assert cpre.analyze_macros(source) == ()


@pytest.mark.parametrize("newline", LINE_BREAKS)
def test_backslash_line_break_splices_a_directive(newline):
    source = f"#if A ||\\{newline} B{newline}#endif{newline}"

    tree = cpre.parse_conditionals(source)

    (directive, endif) = tree.directives
    assert directive.condition_text == f"A ||\\{newline} B"
    assert endif.source_range.start.line == 3


@pytest.mark.parametrize("newline", LINE_BREAKS)
def test_line_comment_ends_at_any_line_break(newline):
    source = f"int a; // comment{newline}#if A{newline}#endif{newline}"

    assert [d.kind for d in cpre.parse_conditionals(source).directives] == ["if", "endif"]


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("ff.c", "\f\n#define W (X || X)\nint y;\n", "\f\n#define W (!!X)\nint y;\n"),
        ("cr.c", "int a;\r#define W (X || X)\n", "int a;\r#define W (!!X)\n"),
    ],
)
def test_issue_examples_report_and_rewrite(tmp_path, capsys, name, content, expected):
    path = tmp_path / name
    path.write_bytes(content.encode())

    assert main(["simplify-macros", str(path)]) == 0
    assert "L2 SIMPLIFY   #define W\n   - (X || X)\n   + (!!X)" in capsys.readouterr().out

    assert main(["simplify-macros", "--check", str(path)]) == 1
    assert main(["simplify-macros", "--diff", str(path)]) == 0
    # The diff splits on ``\n`` only so ``patch`` can apply it; a lone CR stays in its line.
    assert "#define W (!!X)\n" in capsys.readouterr().out
    assert path.read_bytes() == content.encode()

    assert main(["simplify-macros", "--rewrite", str(path)]) == 0
    assert path.read_bytes() == expected.encode()
    assert main(["simplify-macros", "--check", str(path)]) == 0
