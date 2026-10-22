"""``--max-atoms`` / ``--max-bdd-nodes`` / ``--max-work`` on the analysis commands."""

from __future__ import annotations

import json

import pytest

from cpre.cli import main

WIDE = (
    "#if "
    + " && ".join(f"F{i}" for i in range(70))
    + "\n#endif\n#ifdef OK\n#ifdef OK\n#endif\n#endif\n"
)
MACRO = "#define FEAT (A && A)\n"


def _write(tmp_path, text, name="wide.c"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_default_limit_is_exceeded_and_raising_it_completes(tmp_path, capsys):
    path = _write(tmp_path, WIDE)

    assert main([path]) == 2
    captured = capsys.readouterr()
    assert "analysis limit exceeded for atoms: 70 > 64" in captured.err

    assert main(["--max-atoms", "80", path]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "REDUNDANT  #ifdef OK" in captured.out


def test_lowering_a_limit_makes_analysis_incomplete(tmp_path, capsys):
    path = _write(tmp_path, "#if A && B && C\n#endif\n")

    assert main([path]) == 0
    capsys.readouterr()
    assert main(["--max-atoms", "2", path]) == 2
    assert "analysis limit exceeded for atoms: 3 > 2" in capsys.readouterr().err


def test_json_reports_the_limit_diagnostic(tmp_path, capsys):
    path = _write(tmp_path, WIDE)

    assert main(["--json", "--max-atoms", "65", path]) == 2
    document = json.loads(capsys.readouterr().out)
    assert document["complete"] is False
    assert document["incomplete"][0]["limit"] == 65


def test_max_work_and_bdd_nodes_are_forwarded(tmp_path, capsys):
    path = _write(tmp_path, "#if A\n#if A\n#endif\n#endif\n")

    assert main(["--max-work", "1", path]) == 2
    assert "analysis limit exceeded for work" in capsys.readouterr().err
    assert main(["--max-bdd-nodes", "1", path]) == 2
    assert "bdd_nodes" in capsys.readouterr().err


def test_macro_analysis_honors_limits(tmp_path, capsys):
    path = _write(tmp_path, MACRO, "macro.c")

    assert main(["--no-conditionals", "--max-work", "1", path]) == 2
    assert "analysis limit exceeded for work" in capsys.readouterr().err

    assert main(["simplify-macros", "--check", path]) == 1
    capsys.readouterr()
    assert main(["simplify-macros", "--check", "--max-work", "1", path]) == 2
    assert "analysis limit exceeded for work" in capsys.readouterr().err

    assert main(["analyze-macros", "--max-work", "1", path]) == 2
    capsys.readouterr()


@pytest.mark.parametrize(
    "argv",
    [["--max-atoms", "0"], ["simplify-macros", "--max-work", "-1"], ["--max-bdd-nodes", "x"]],
)
def test_invalid_limit_is_a_usage_error(tmp_path, capsys, argv):
    path = _write(tmp_path, MACRO, "macro.c")

    with pytest.raises(SystemExit) as excinfo:
        main([*argv, path])
    assert excinfo.value.code == 2
    assert "error" in capsys.readouterr().err


def test_cover_still_accepts_limits(tmp_path, capsys):
    path = _write(tmp_path, "#if A\n#endif\n")

    assert main(["cover", "--max-work", "1", path]) == 2
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["cover", "--max-atoms", "0", path])
    capsys.readouterr()
