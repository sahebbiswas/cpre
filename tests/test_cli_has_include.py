from __future__ import annotations

import pytest

import cpre
from cpre.cli import main
from cpre.cli.has_include_answers import parse_has_include_answers
from cpre.includes import IncludeForm

SOURCE = (
    "#if __has_include(<opt.h>)\nint opt;\n#endif\n"
    '#if __has_include("local.h")\nint local;\n#endif\n'
    "#if __has_include(<found.h>)\nint found;\n#endif\n"
)


@pytest.fixture
def tree(tmp_path):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "found.h").write_text("")
    target = tmp_path / "t.c"
    target.write_bytes(SOURCE.encode())
    return inc, target


def _kept(out):
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_explicit_answers_match_the_api_include_query(tmp_path, capsys):
    target = tmp_path / "t.c"
    text = SOURCE.replace("#if __has_include(<found.h>)\nint found;\n#endif\n", "")
    target.write_bytes(text.encode())
    expected = cpre.preprocess_source(
        text,
        filename=str(target),
        include_query=lambda q: {"<opt.h>": True, '"local.h"': False}.get(
            f"<{q.header}>" if q.form is IncludeForm.ANGLE else f'"{q.header}"'
        ),
    )
    argv = ["preprocess", "--has-include", "<opt.h>=1", "--has-include", '"local.h"=0', str(target)]
    assert main(argv) == 0
    assert capsys.readouterr().out == expected.source


def test_angle_and_quoted_forms_are_distinct(tmp_path, capsys):
    target = tmp_path / "t.c"
    text = '#if __has_include(<x.h>)\nint a;\n#endif\n#if __has_include("x.h")\nint b;\n#endif\n'
    target.write_bytes(text.encode())
    assert (
        main(["preprocess", "--has-include", "<x.h>=1", "--has-include", '"x.h"=0', str(target)])
        == 0
    )
    assert _kept(capsys.readouterr().out) == ["int a;"]


def test_macro_expanded_spelling_is_matched(tmp_path, capsys):
    target = tmp_path / "t.c"
    target.write_bytes(b"#define H <opt.h>\n#if __has_include(H)\nint yes;\n#endif\n")
    assert main(["preprocess", "--has-include", "<opt.h>=1", str(target)]) == 0
    assert "int yes;" in capsys.readouterr().out


def test_unanswered_query_stays_incomplete_without_search(tree, capsys):
    _, target = tree
    assert main(["preprocess", "--has-include", "<opt.h>=1", str(target)]) == 2
    assert 'include availability is unknown for "local.h"' in capsys.readouterr().err


def test_search_answers_the_rest_and_explicit_answers_win(tree, capsys):
    inc, target = tree
    base = ["preprocess", "-I", str(inc), "--has-include-from-search"]
    assert (
        main([*base, "--has-include", "<opt.h>=1", "--has-include", '"local.h"=0', str(target)])
        == 0
    )
    assert _kept(capsys.readouterr().out) == ["int opt;", "int found;"]

    # An explicit 0 overrides a header the search would find.
    argv = [*base, "--has-include", "<found.h>=0", "--has-include", "<opt.h>=0"]
    assert main([*argv, "--has-include", '"local.h"=0', str(target)]) == 0
    assert _kept(capsys.readouterr().out) == []


def test_explicit_answers_need_no_include_search(tree, capsys):
    _, target = tree
    argv = ["preprocess", "--has-include", "<opt.h>=1", "--has-include", '"local.h"=1']
    assert main([*argv, "--has-include", "<found.h>=1", str(target)]) == 0
    assert _kept(capsys.readouterr().out) == ["int opt;", "int local;", "int found;"]


@pytest.mark.parametrize(
    "value, message",
    [
        ("opt.h=1", "expected '<header>=1'"),
        ("<opt.h>", "expected '<header>=1'"),
        ("<opt.h>=2", "expected '<header>=1'"),
        ("<>=1", "expected '<header>=1'"),
        ('"local.h"=yes', "expected '<header>=1'"),
    ],
)
def test_malformed_values_fail_with_exit_status_2(tree, capsys, value, message):
    _, target = tree
    assert main(["preprocess", "--has-include", value, str(target)]) == 2
    assert message in capsys.readouterr().err


def test_conflicting_answers_fail_but_repeats_are_fine(tree, capsys):
    _, target = tree
    assert (
        main(["preprocess", "--has-include", "<a.h>=1", "--has-include", "<a.h>=0", str(target)])
        == 2
    )
    assert "conflicting answers for <a.h>" in capsys.readouterr().err
    assert parse_has_include_answers(["<a.h>=1", "<a.h>=1"]) == {(IncludeForm.ANGLE, "a.h"): True}


def test_malformed_answers_fail_even_with_list_unknown_macros(tree, capsys):
    _, target = tree
    assert main(["preprocess", "--list-unknown-macros", "--has-include", "bogus", str(target)]) == 2
    assert "--has-include: expected" in capsys.readouterr().err
