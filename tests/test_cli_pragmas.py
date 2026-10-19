"""``--consume-pragma`` on ``cpre preprocess`` and ``cpre cover`` (issue #197)."""

from __future__ import annotations

import json

import pytest

from cpre.cli import main

HEADER = "#pragma once\n#pragma pack(push, 1)\n#ifdef A\nint a;\n#endif\n#pragma pack(pop)\n"


def _write(tmp_path, text=HEADER, name="h.h"):
    path = tmp_path / name
    # Bytes, so Windows does not translate the line endings the output assertions compare.
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def test_pragma_still_fails_closed_without_the_option(tmp_path, capsys):
    path = _write(tmp_path)

    assert main(["preprocess", path, "-D", "A"]) == 2
    err = capsys.readouterr().err
    assert f"{path}: line 1: #pragma preprocessing directive is not supported" in err
    assert f"{path}: hint: pass --consume-pragma PREFIX" in err


def test_prefix_matches_whole_tokens_and_consumes_the_pragma(tmp_path, capsys):
    path = _write(tmp_path)

    assert main(["preprocess", path, "-D", "A", "--consume-pragma", "once"]) == 2
    err = capsys.readouterr().err
    assert "line 2: reachable pragma is unsupported by the caller's pragma handler" in err
    assert "pack(push, 1)" in err
    assert f"{path}: hint: add --consume-pragma pack to accept this pragma" in err

    argv = ["preprocess", path, "-D", "A", "--compact"]
    assert main([*argv, "--consume-pragma", "once", "--consume-pragma", "pack"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "int a;\n"
    assert captured.err == ""


def test_prefix_does_not_match_a_longer_first_token(tmp_path, capsys):
    path = _write(tmp_path, "#pragma packed\nint x;\n")

    assert main(["preprocess", path, "--consume-pragma", "pack"]) == 2
    assert "unsupported by the caller's pragma handler: packed" in capsys.readouterr().err


def test_multi_token_prefix_and_whitespace_normalization(tmp_path, capsys):
    path = _write(tmp_path, '#pragma  GCC   diagnostic ignored "-Wunused"\nint x;\n')

    assert main(["preprocess", path, "--consume-pragma", "GCC diagnostic", "--compact"]) == 0
    assert capsys.readouterr().out == "int x;\n"
    assert main(["preprocess", path, "--consume-pragma", "GCC warning"]) == 2
    capsys.readouterr()


def test_operator_pragmas_and_inactive_branches(tmp_path, capsys):
    path = _write(tmp_path, '_Pragma("message(\\"hi\\")")\n#if 0\n#pragma never\n#endif\nint x;\n')

    assert main(["preprocess", path, "--consume-pragma", "message", "--compact"]) == 0
    # The operator is blanked in place, so its line stays (as spaces) before the code.
    assert capsys.readouterr().out.split() == ["int", "x;"]
    assert main(["preprocess", path]) == 2
    assert "hint: pass --consume-pragma PREFIX" in capsys.readouterr().err


def test_json_output_has_no_hint_and_reports_diagnostics(tmp_path, capsys):
    path = _write(tmp_path)

    assert main(["preprocess", path, "-D", "A", "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["complete"] is False
    assert "hint:" not in captured.out


@pytest.mark.parametrize("value", ["", "   ", "/* c */"])
def test_invalid_prefix_is_a_usage_error(tmp_path, capsys, value):
    path = _write(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(["preprocess", path, "--consume-pragma", value])
    assert excinfo.value.code == 2
    assert "is not a pragma payload prefix" in capsys.readouterr().err


def test_cover_accepts_consumed_pragmas_and_hints_otherwise(tmp_path, capsys):
    path = _write(tmp_path)

    assert main(["cover", path]) == 1
    captured = capsys.readouterr()
    assert f"{path}: hint: pass --consume-pragma PREFIX" in captured.err
    assert "hint:" not in captured.out

    assert main(["cover", path, "--consume-pragma", "once", "--consume-pragma", "pack"]) == 0
    captured = capsys.readouterr()
    assert "-D A=1" in captured.out
    assert captured.err == ""


def test_included_header_pragmas_use_the_same_policy(tmp_path, capsys):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "lib.h").write_bytes(b"#pragma pack(push, 1)\n#define LIB 1\n")
    main_c = _write(tmp_path, '#include "lib.h"\n#if LIB\nint ok;\n#endif\n', "main.c")

    assert main(["preprocess", main_c, "-I", str(inc), "--compact"]) == 2
    capsys.readouterr()
    argv = ["preprocess", main_c, "-I", str(inc), "--compact", "--consume-pragma", "pack"]
    assert main(argv) == 0
    assert capsys.readouterr().out == "int ok;\n"
