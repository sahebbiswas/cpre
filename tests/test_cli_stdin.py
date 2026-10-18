"""Standard input as a source (``-``) for the source commands (#105)."""

from __future__ import annotations

import io
import json
import sys

import pytest

from cpre.cli import main

SOURCE = "#define A (X || X)\n#if X\n#if !X\nint dead;\n#endif\n#endif\n"


def _stdin(monkeypatch, data):
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))


def _run(monkeypatch, capsys, argv, data=SOURCE):
    _stdin(monkeypatch, data)
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_analysis_reads_stdin_and_names_it(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["-"])
    assert code == 0 and err == ""
    assert "dead" in out
    code, out, _ = _run(monkeypatch, capsys, ["--json", "-"])
    assert json.loads(out)["path"] == "<stdin>"


def test_stdin_analysis_equals_file_analysis(monkeypatch, capsys, tmp_path):
    path = tmp_path / "t.c"
    path.write_bytes(SOURCE.encode())
    _, from_stdin, _ = _run(monkeypatch, capsys, ["--json", "-"])
    assert main(["--json", str(path)]) == 0
    from_file = json.loads(capsys.readouterr().out)
    from_stdin = json.loads(from_stdin)
    assert from_stdin.pop("path") == "<stdin>" and from_file.pop("path") == str(path)
    assert from_stdin == from_file


def test_malformed_stdin_exits_2_with_diagnostics_on_stderr(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["-"], "#if X\n")
    assert code == 2 and out == ""
    assert err.startswith("<stdin>: ")
    code, _, err = _run(monkeypatch, capsys, ["-"], b"\xff\xfe")
    assert code == 2 and err.startswith("<stdin>: ")


def test_stdin_can_be_mixed_with_files(monkeypatch, capsys, tmp_path):
    path = tmp_path / "t.c"
    path.write_bytes(SOURCE.encode())
    code, out, _ = _run(monkeypatch, capsys, ["--json", str(path), "-"])
    assert code == 0
    assert [f["path"] for f in json.loads(out)["files"]] == [str(path), "<stdin>"]


def test_stdin_twice_is_a_usage_error(monkeypatch, capsys):
    _stdin(monkeypatch, SOURCE)
    with pytest.raises(SystemExit) as caught:
        main(["-", "-"])
    assert caught.value.code == 2
    assert "only once" in capsys.readouterr().err


def test_a_file_named_dash_is_reachable_with_a_path_prefix(capsys, tmp_path, monkeypatch):
    (tmp_path / "-").write_bytes(SOURCE.encode())
    monkeypatch.chdir(tmp_path)
    assert main(["--json", "./-"]) == 0
    assert json.loads(capsys.readouterr().out)["path"] == "-"


def test_simplify_macros_modes_work_on_stdin(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, ["simplify-macros", "-"])
    assert code == 0 and "(!!X)" in out
    code, out, err = _run(monkeypatch, capsys, ["simplify-macros", "--diff", "-"])
    assert code == 0 and out.startswith("--- <stdin>\n+++ <stdin>\n")
    code, out, err = _run(monkeypatch, capsys, ["simplify-macros", "--check", "-"])
    assert code == 1 and "would rewrite <stdin>" in err
    code, out, _ = _run(monkeypatch, capsys, ["simplify-macros", "--check", "--json", "-"])
    assert json.loads(out)["path"] == "<stdin>" and code == 1


def test_simplify_macros_preserves_crlf_in_the_diff(monkeypatch, capsys):
    code, out, _ = _run(
        monkeypatch, capsys, ["simplify-macros", "--diff", "-"], SOURCE.replace("\n", "\r\n")
    )
    assert code == 0 and "-#define A (X || X)\r\n" in out


@pytest.mark.parametrize("flag", ["--rewrite", "--in-place"])
def test_rewriting_stdin_is_rejected_before_reading_it(monkeypatch, capsys, flag):
    _stdin(monkeypatch, SOURCE)
    with pytest.raises(SystemExit) as caught:
        main(["simplify-macros", flag, "-"])
    assert caught.value.code == 2
    assert "cannot be rewritten" in capsys.readouterr().err
    assert sys.stdin.buffer.tell() == 0


def test_preprocess_pipes_the_transformed_source(monkeypatch, capsys):
    code, out, err = _run(
        monkeypatch, capsys, ["preprocess", "-D", "X=1", "-"], "#if X\nyes\n#endif\n"
    )
    assert code == 0 and err == "" and "yes" in out
    code, out, err = _run(monkeypatch, capsys, ["preprocess", "-"], "#if X\nyes\n#endif\n")
    assert code == 2 and out == "" and err.startswith("<stdin>: ")


def test_preprocess_file_macro_and_json_use_the_logical_name(monkeypatch, capsys):
    _, out, _ = _run(monkeypatch, capsys, ["preprocess", "-"], "__FILE__\n")
    assert '"<stdin>"' in out
    _, out, _ = _run(monkeypatch, capsys, ["preprocess", "--json", "-"], "int x;\n")
    assert json.loads(out)["file"] == "<stdin>"
    _, out, _ = _run(
        monkeypatch, capsys, ["preprocess", "--list-unknown-macros", "-"], "#if X\n#endif\n"
    )
    assert out.startswith("X\t")


def test_preprocess_quoted_includes_from_stdin_search_the_working_directory(
    monkeypatch, capsys, tmp_path
):
    (tmp_path / "h.h").write_bytes(b"#define FROM_H 7\n")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run(
        monkeypatch, capsys, ["preprocess", "-I", ".", "-"], '#include "h.h"\nFROM_H\n'
    )
    assert code == 0 and "7" in out


def test_dump_macros_does_not_consume_stdin(monkeypatch, capsys):
    _stdin(monkeypatch, SOURCE)
    assert main(["preprocess", "--dump-macros", "--json", "-D", "A", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["file"] == "<stdin>"
    assert sys.stdin.buffer.tell() == 0


def test_cover_reads_stdin(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, ["cover", "-"], "#if A\nx\n#endif\n")
    assert code == 0 and out.splitlines() == [
        "--unknown-names undefined -D A=1",
        "--unknown-names undefined -U A",
    ]
    code, out, _ = _run(monkeypatch, capsys, ["cover", "--json", "-"], "#if A\nx\n#endif\n")
    assert json.loads(out)["file"] == "<stdin>"
    code, _, err = _run(monkeypatch, capsys, ["cover", "-"], "#if\n#endif\n")
    assert code == 2 and err.startswith("<stdin>: ")


def test_real_pipeline_through_a_subprocess():
    import subprocess

    run = subprocess.run(
        [sys.executable, "-m", "cpre", "simplify-macros", "--diff", "-"],
        input=SOURCE.encode(),
        capture_output=True,
        check=True,
    )
    assert run.stdout.startswith(b"--- <stdin>\n+++ <stdin>\n") and run.stderr == b""
    failed = subprocess.run(
        [sys.executable, "-m", "cpre", "preprocess", "-"],
        input=b"#if X\n",
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 2 and failed.stdout == b"" and failed.stderr.startswith(b"<stdin>:")


@pytest.mark.skipif(sys.platform == "win32", reason="'<' and '>' are not valid in Windows names")
def test_a_real_file_named_like_stdin_is_still_a_file(monkeypatch, capsys, tmp_path):
    (tmp_path / "<stdin>").write_bytes(b"#if FROM_FILE\n#endif\n")
    monkeypatch.chdir(tmp_path)
    _stdin(monkeypatch, "#if FROM_STDIN\n#endif\n")
    assert main(["preprocess", "--list-unknown-macros", "./<stdin>"]) == 0
    assert capsys.readouterr().out.startswith("FROM_FILE\t")
    assert main(["preprocess", "--list-unknown-macros", "-"]) == 0
    assert capsys.readouterr().out.startswith("FROM_STDIN\t")
    assert main(["--json", "--verbose", "<stdin>", "-"]) == 0
    assert len(json.loads(capsys.readouterr().out)["files"]) == 2
