from __future__ import annotations

from pathlib import Path

import pytest

import cpre
from cpre.cli import main


def _write_source(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def test_preprocess_cli_emits_canonical_source_from_explicit_configuration(tmp_path, capsys):
    """Emit the canonical source selected by an explicit macro configuration."""
    source = tmp_path / "source.c"
    _write_source(
        source,
        "#ifdef FEATURE\nint enabled;\n#else\nint disabled;\n#endif\n",
    )
    expected = cpre.preprocess_source(
        source.read_text(encoding="utf-8"),
        filename=str(source),
        configuration=cpre.MacroConfiguration(presence=["FEATURE"]),
    )
    assert expected.complete
    assert main(["preprocess", str(source), "-DFEATURE"]) == 0
    assert capsys.readouterr().out == expected.source


def test_preprocess_cli_rejects_equals_in_undef_argument(tmp_path, capsys):
    """Reject an explicit equals sign in an undefined-macro argument."""
    source = tmp_path / "source.c"
    _write_source(
        source,
        "#ifdef DISABLED\nint no;\n#endif\n",
    )
    assert main(["preprocess", str(source), "-U", "DISABLED="]) == 2
    assert "--undef accepts only a macro name" in capsys.readouterr().err


def test_preprocess_cli_preserves_utf8_and_crlf_output(tmp_path, capsys):
    """Preserve UTF-8 text and CRLF line endings in canonical output."""
    source = tmp_path / "source.c"
    source.write_bytes(
        '#ifdef FEATURE\r\nconst char *text = "caf\u00e9";\r\n#endif\r\n'.encode("utf-8")
    )
    assert main(["preprocess", str(source), "-DFEATURE"]) == 0
    canonical = capsys.readouterr().out
    assert 'const char *text = "caf\u00e9";\r\n' in canonical
    assert canonical.endswith("\r\n")

    assert main(["preprocess", str(source), "-DFEATURE", "--compact"]) == 0
    assert capsys.readouterr().out == 'const char *text = "caf\u00e9";\r\n'


def test_preprocess_cli_preserves_no_final_newline_and_source_mapping(tmp_path, capsys):
    """Preserve a source without a final newline while retaining source mappings."""
    source = tmp_path / "source.c"
    _write_source(source, "#ifdef FEATURE\nint value = 42;\n#endif")
    expected = cpre.preprocess_source(
        source.read_text(encoding="utf-8"),
        filename=str(source),
        configuration=cpre.MacroConfiguration(presence=["FEATURE"]),
    )
    assert expected.complete
    assert expected.source_map is not None
    assert main(["preprocess", str(source), "-DFEATURE"]) == 0
    assert capsys.readouterr().out == expected.source


def test_preprocess_cli_supports_integer_replacement_and_undefined(tmp_path, capsys):
    """Resolve integer definitions and explicit undefined macros."""
    source = tmp_path / "source.c"
    _write_source(
        source,
        "#if LEVEL == 2\nint level_two;\n#endif\n#ifdef DISABLED\nint no;\n#endif\n",
    )
    assert main(["preprocess", str(source), "-D", "LEVEL=2", "-U", "DISABLED"]) == 0
    output = capsys.readouterr().out
    assert "int level_two;" in output
    assert "int no;" not in output


def test_preprocess_cli_supports_deterministic_standard_macro_context(tmp_path, capsys):
    """Use caller-supplied deterministic predefined macro values."""
    source = tmp_path / "source.c"
    _write_source(source, "#if __STDC__\nint hosted;\n#endif\n")
    assert main(["preprocess", str(source), "--standard-macro", "__STDC__=1"]) == 0
    assert "int hosted;" in capsys.readouterr().out


def test_preprocess_cli_supports_unknown_names_policy(tmp_path, capsys):
    """Support closed-world handling with --unknown-names undefined."""
    source = tmp_path / "source.c"
    _write_source(
        source,
        "#if UNKNOWN\nint active;\n#else\nint inactive;\n#endif\n",
    )
    assert main(["preprocess", str(source), "--unknown-names", "undefined"]) == 0
    assert "int inactive;" in capsys.readouterr().out


def test_preprocess_cli_reports_incomplete_without_partial_output(tmp_path, capsys):
    """Report unresolved conditions without emitting partial output."""
    source = tmp_path / "source.c"
    _write_source(
        source,
        "#if UNKNOWN_FEATURE\nint selected;\n#endif\n",
    )
    assert main(["preprocess", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "condition is not determined" in captured.err.lower()
        or "unknown_feature" in captured.err.lower()
        or "unresolved" in captured.err.lower()
    )


def test_preprocess_cli_compact_is_explicit(tmp_path, capsys):
    """Keep canonical output separate from explicitly requested compact output."""
    source = tmp_path / "source.c"
    _write_source(source, "#if 0\ndead\n#endif\nkept\n")
    assert main(["preprocess", str(source)]) == 0
    canonical = capsys.readouterr().out
    assert "kept" in canonical
    assert "dead" not in canonical
    assert canonical.count("\n") == 4
    assert main(["preprocess", str(source), "--compact"]) == 0
    assert capsys.readouterr().out == "kept\n"


def test_preprocess_cli_compact_rejects_negative_limit(tmp_path, capsys):
    """Reject a negative compact blank-line limit."""
    source = tmp_path / "source.c"
    _write_source(source, "#if 0\ndead\n#endif\nkept\n")
    assert main(["preprocess", str(source), "--compact", "--max-blank-lines", "-1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "non-negative" in captured.err


def test_preprocess_cli_rejects_batch_inputs(tmp_path, capsys):
    """Reject multiple source files rather than implicitly enabling batch mode."""
    first = tmp_path / "one.c"
    second = tmp_path / "two.c"
    _write_source(first, "int one;\n")
    _write_source(second, "int two;\n")
    with pytest.raises(SystemExit) as exc:
        main(["preprocess", str(first), str(second)])
    assert exc.value.code == 2
    assert "exactly one source file" in capsys.readouterr().err


def test_preprocess_cli_rejects_directory_input(tmp_path, capsys):
    """Reject directory inputs rather than searching recursively."""
    with pytest.raises(SystemExit) as exc:
        main(["preprocess", str(tmp_path)])
    assert exc.value.code == 2
    assert "directory inputs are not supported" in capsys.readouterr().err


def test_preprocess_cli_rejects_missing_sources(capsys):
    """Reject missing source arguments with parser error."""
    with pytest.raises(SystemExit) as exc:
        main(["preprocess"])
    assert exc.value.code == 2


def test_preprocess_cli_rejects_standard_macro_without_value(tmp_path, capsys):
    """Reject standard macro missing =VALUE."""
    source = tmp_path / "source.c"
    _write_source(source, "int x;\n")
    assert main(["preprocess", str(source), "--standard-macro", "__STDC__"]) == 2
    assert "--standard-macro requires NAME=VALUE" in capsys.readouterr().err


def test_preprocess_cli_rejects_invalid_define_name(tmp_path, capsys):
    """Reject invalid macro identifier in --define."""
    source = tmp_path / "source.c"
    _write_source(source, "int x;\n")
    assert main(["preprocess", str(source), "-D", "123=foo"]) == 2
    assert "invalid macro name '123'" in capsys.readouterr().err


def test_preprocess_cli_reports_missing_file_error(tmp_path, capsys):
    """Report file not found error with exit code 2."""
    missing = tmp_path / "missing.c"
    assert main(["preprocess", str(missing)]) == 2
    assert str(missing) in capsys.readouterr().err


def test_preprocess_cli_help_describes_concrete_workflow(capsys):
    """Document the concrete preprocessing options in command help."""
    with pytest.raises(SystemExit) as exc:
        main(["preprocess", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "concrete preprocessing" in output.lower()
    assert "--define" in output
    assert "--compact" in output
