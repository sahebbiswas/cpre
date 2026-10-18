"""``cpre cover`` prints and serializes branch-covering configurations (#126)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

import pytest

from cpre.cli import main

SOURCE = (
    "#if A && !B\nint a;\n#elif C\nint c;\n#endif\n"
    "#if X\n#if !X\nint dead;\n#endif\n#endif\n"
    "#if V >= 3\nint v;\n#endif\n"
)


def _write(tmp_path, text, name="t.c"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


@pytest.fixture
def target(tmp_path):
    return _write(tmp_path, SOURCE)


def _cover(capsys, *argv):
    code = main(["cover", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_text_lists_flag_lines_then_uncovered_outcomes(capsys, target):
    code, out, err = _cover(capsys, target)
    assert code == 0 and err == ""
    lines = out.splitlines()
    flag_lines = [line for line in lines if not line.startswith("#")]
    assert flag_lines and all(line.startswith("--unknown-names undefined") for line in flag_lines)
    assert lines[-1].startswith("# unsatisfiable: line 7: #if: ")


def test_each_printed_line_reproduces_its_coverage_in_preprocess(capsys, target):
    """Passing a configuration's flags to ``cpre preprocess`` selects the outcomes it covers."""
    _, out, _ = _cover(capsys, target)
    seen = set()
    for line in out.splitlines():
        if line.startswith("#"):
            continue
        assert main(["preprocess", *shlex.split(line), target]) == 0
        rendered = capsys.readouterr().out
        seen.update(token for token in ("int a;", "int c;", "int v;") if token in rendered)
    assert seen == {"int a;", "int c;", "int v;"}


def test_json_document_fields_and_cross_references(capsys, target):
    code, out, _ = _cover(capsys, "--json", target)
    data = json.loads(out)
    assert code == 0 and data["schema"] == "cpre.cover/1" and data["complete"] is True
    assert set(data) == {"schema", "file", "complete", "configurations", "outcomes", "incomplete"}
    assert data["incomplete"] is None
    for index, configuration in enumerate(data["configurations"]):
        assert set(configuration) == {"index", "flags", "required", "dont_care", "covers"}
        assert configuration["index"] == index
        assert all(set(a) == {"kind", "symbol", "value"} for a in configuration["required"])
        for covered in configuration["covers"]:
            assert data["outcomes"][covered]["status"] == "covered"
    for outcome in data["outcomes"]:
        assert set(outcome) == {
            "index", "group_line", "branch_line", "directive", "status", "configuration", "reason",
        }  # fmt: skip
        if outcome["status"] == "covered":
            assert outcome["configuration"] is not None and outcome["reason"] is None
    assert any(o["status"] == "unsatisfiable" for o in data["outcomes"])


def test_exit_status_one_when_an_outcome_is_not_covered(capsys, tmp_path):
    path = _write(tmp_path, "#if VERSION % 2 == 1\nint odd;\n#endif\n")
    code, out, _ = _cover(capsys, path)
    assert code == 1
    assert any(line.startswith("# not covered: ") for line in out.splitlines())
    code, out, _ = _cover(capsys, "--json", path)
    assert code == 1
    statuses = {o["status"] for o in json.loads(out)["outcomes"]}
    assert "not_covered" in statuses


def test_incomplete_analysis_exits_2_with_null_results(capsys, target):
    code, out, err = _cover(capsys, "--max-atoms", "1", target)
    assert code == 2 and out == "" and err
    code, out, _ = _cover(capsys, "--max-atoms", "1", "--json", target)
    data = json.loads(out)
    assert code == 2 and data["complete"] is False
    assert data["configurations"] is None and data["outcomes"] is None
    assert data["incomplete"]["resource"] == "atoms"


def test_errors_exit_2(capsys, tmp_path):
    assert main(["cover", str(tmp_path / "missing.c")]) == 2
    assert "missing.c" in capsys.readouterr().err
    bad = _write(tmp_path, "#if\n#endif\n", "bad.c")
    assert main(["cover", bad]) == 2
    assert capsys.readouterr().err
    assert main(["cover", "--max-atoms", "0", bad]) == 2
    capsys.readouterr()


def test_usage_errors(capsys, tmp_path):
    for argv in (
        [str(tmp_path)],
        ["--has-include-from-search", str(tmp_path / "x.c")],
    ):
        with pytest.raises(SystemExit) as caught:
            main(["cover", *argv])
        assert caught.value.code == 2
    capsys.readouterr()


def test_standard_macro_and_include_options_are_honored(capsys, tmp_path):
    _write(tmp_path, "#define FROM_HEADER 1\n", "h.h")
    path = _write(
        tmp_path, '#include "h.h"\n#if __STDC_VERSION__ >= 201112L\nint modern;\n#endif\n'
    )
    code, out, _ = _cover(
        capsys, "--standard-macro", "__STDC_VERSION__=201710L", "-I", str(tmp_path), "--json", path
    )
    outcomes = json.loads(out)["outcomes"]
    # The context fixes the value, so the #if branch is selected; the "no branch" outcome
    # cannot be, which is reported rather than hidden (exit 1).
    assert code == 1
    assert [(o["branch_line"], o["status"]) for o in outcomes] == [
        (2, "covered"),
        (None, "not_covered"),
    ]
    assert main(["cover", "--skip-includes", path]) in (0, 1)
    capsys.readouterr()


def test_output_is_identical_across_hash_seeds(target):
    def run(seed):
        return subprocess.run(
            [sys.executable, "-m", "cpre", "cover", "--json", target],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout  # fmt: skip

    assert run("0") == run("1") == run("random")


def test_source_without_conditionals_has_no_configurations(capsys, tmp_path):
    code, out, _ = _cover(capsys, _write(tmp_path, "int x;\n"))
    assert code == 0 and out == ""
