"""Tests for CLI macro simplification and symbolic-literal options (issue #79)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cpre.cli import main


def test_cli_macros_text_output(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n#define BUFFER_SIZE 1024\n")

    code = main([str(source), "--macros"])
    assert code == 0
    captured = capsys.readouterr()
    assert "#define FEAT_1 (0 && A) || B -> (B)" in captured.out
    assert "BUFFER_SIZE" not in captured.out


def test_cli_macros_verbose_includes_all_macros(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text(
        "#define FEAT_1 (0 && A) || B\n#define BUFFER_SIZE 1024\n#define FEAT_2 (A || B)\n"
    )

    code = main([str(source), "--macros", "--verbose"])
    assert code == 0
    captured = capsys.readouterr()
    assert "#define FEAT_1 (0 && A) || B -> (B)" in captured.out
    assert "BUFFER_SIZE skipped: non-Boolean integer literal" in captured.out
    assert "#define FEAT_2 (A || B)" in captured.out


def test_cli_macros_json_ordinary_semantics(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n")

    code = main([str(source), "--macros", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(source)
    assert data["semantics"] == "ordinary"
    assert data["symbolic_literals"] == []
    assert len(data["macros"]) == 1
    macro = data["macros"][0]
    assert macro["name"] == "FEAT_1"
    assert macro["candidate"] is True
    assert macro["simplified"] is True
    assert macro["replacement"] == "(B)"
    assert macro["semantics"] == "ordinary"


def test_cli_symbolic_literal_zero(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || (0 && B)\n")

    code = main([str(source), "--macros", "--symbolic-literal", "0"])
    assert code == 0
    captured = capsys.readouterr()
    assert "#define FEAT_1 (0 && A) || (0 && B) -> (0 && (A || B))" in captured.out


def test_cli_symbolic_zero_flag(tmp_path, capsys):
    source = tmp_path / "test.c"
    # (0 && A) || B is already simplest under symbolic-zero semantics
    source.write_text("#define FEAT_1 (0 && A) || B\n")

    code = main([str(source), "--symbolic-zero"])
    assert code == 0
    captured = capsys.readouterr()
    assert "No notable conditional directives or simplifiable macros found." in captured.out


def test_cli_symbolic_literal_json(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || (0 && B)\n")

    code = main([str(source), "--symbolic-literal", "0", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["semantics"] == "symbolic-literal"
    assert data["symbolic_literals"] == [0]
    assert data["macros"][0]["replacement"] == "(0 && (A || B))"


def test_cli_symbolic_literal_unsupported_rejected(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT 0 && A\n")

    with pytest.raises(SystemExit) as excinfo:
        main([str(source), "--symbolic-literal", "1"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "unsupported symbolic literal 1" in captured.err


def test_cli_unified_both_conditionals_and_macros(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n")

    code = main([str(source)])
    assert code == 0
    captured = capsys.readouterr()
    # Both conditional findings and macro simplification findings are present
    assert "#elif A [dead]" in captured.out
    assert "#define FEAT_1 (0 && A) || B -> (B)" in captured.out


def test_cli_disable_flags(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n")

    # --no-macros: only conditionals
    main([str(source), "--no-macros"])
    out1 = capsys.readouterr().out
    assert "#elif A [dead]" in out1
    assert "FEAT_1" not in out1

    # --no-conditionals: only macros
    main([str(source), "--no-conditionals"])
    out2 = capsys.readouterr().out
    assert "#elif A" not in out2
    assert "#define FEAT_1 (0 && A) || B -> (B)" in out2


def test_cli_sarif_with_symbolic_options(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#if A\n#elif A\n#endif\n#define FEAT 0 && A\n")

    code = main([str(source), "--sarif", "--symbolic-zero"])
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) == 1


def test_cli_macros_fail_on_findings(tmp_path):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n")

    # With findings: status 1
    code = main([str(source), "--macros", "--fail-on-findings"])
    assert code == 1

    # In symbolic zero mode, (0 && A) || B has no findings: status 0
    code_sym = main([str(source), "--symbolic-zero", "--fail-on-findings"])
    assert code_sym == 0


def test_cli_macros_batch_recursive(tmp_path, capsys):
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = tmp_path / "f1.c"
    f2 = sub / "f2.h"
    f1.write_text("#define M1 (A && A)\n")
    f2.write_text("#define M2 (B || 0)\n")

    code = main([str(tmp_path), "--recursive", "--macros"])
    assert code == 0
    captured = capsys.readouterr()
    assert "f1.c" in captured.out
    assert "#define M1 (A && A) -> (A)" in captured.out
    assert "f2.h" in captured.out
    assert "#define M2 (B || 0) -> (B)" in captured.out


def test_cli_json_disabled_conditionals_emits_empty_groups(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n")

    code = main([str(source), "--json", "--no-conditionals"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(source)
    assert "groups" in data
    assert data["groups"] == []
    assert isinstance(data["groups"], list)
    assert "macros" in data
    assert len(data["macros"]) == 1


def test_cli_json_disabled_macros_emits_empty_macros(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n")

    code = main([str(source), "--json", "--no-macros"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(source)
    assert "groups" in data
    assert len(data["groups"]) == 1
    assert "macros" in data
    assert data["macros"] == []
    assert isinstance(data["macros"], list)


def test_cli_json_batch_disabled_analyses_shape(tmp_path, capsys):
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = sub / "f1.c"
    f1.write_text("#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n")

    code = main([str(tmp_path), "--recursive", "--json", "--no-conditionals"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["files"]) == 1
    file_entry = data["files"][0]
    assert file_entry["groups"] == []
    assert len(file_entry["macros"]) == 1
