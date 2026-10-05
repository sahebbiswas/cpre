from __future__ import annotations

import cpre
from cpre.cli import main


def test_preprocess_cli_emits_canonical_source_from_explicit_configuration(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text(\n        "#if FEATURE\nint enabled;\n#else\nint disabled;\n#endif\n",\n        encoding="utf-8",\n    )
    expected = cpre.preprocess_source(
        source.read_text(encoding="utf-8"),
        filename=str(source),
        configuration=cpre.MacroConfiguration(presence=["FEATURE"]),
    )
    assert expected.complete
    assert main(["preprocess", str(source), "-DFEATURE"]) == 0
    assert capsys.readouterr().out == expected.source


def test_preprocess_cli_rejects_equals_in_undef_argument(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text(\n        "#ifdef DISABLED\nint no;\n#endif\n",\n        encoding="utf-8",\n    )
    assert main(["preprocess", str(source), "-U", "DISABLED="]) == 2
    assert "--undef accepts only a macro name" in capsys.readouterr().err


def test_preprocess_cli_preserves_utf8_and_crlf_output(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_bytes("#if FEATURE\r\nconst char *text = \"caf\u00e9\";\r\n#endif\r\n".encode("utf-8"))
    assert main(["preprocess", str(source), "-DFEATURE"]) == 0
    assert capsys.readouterr().out == 'const char *text = "caf\u00e9";\r\n'


def test_preprocess_cli_supports_integer_replacement_and_undefined(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text(\n        "#if LEVEL == 2\nint level_two;\n#endif\n#ifdef DISABLED\nint no;\n#endif\n",\n        encoding="utf-8",\n    )
    assert main(["preprocess", str(source), "-D", "LEVEL=2", "-U", "DISABLED"]) == 0
    output = capsys.readouterr().out
    assert "int level_two;" in output
    assert "int no;" not in output


def test_preprocess_cli_supports_deterministic_standard_macro_context(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text("#if __STDC__\nint hosted;\n#endif\n", encoding="utf-8")
    assert main(["preprocess", str(source), "--standard-macro", "__STDC__=1"]) == 0
    assert "int hosted;" in capsys.readouterr().out


def test_preprocess_cli_reports_incomplete_without_partial_output(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text(\n        "#if UNKNOWN_FEATURE\nint selected;\n#endif\n",\n        encoding="utf-8",\n    )
    assert main(["preprocess", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "UNKNOWN_FEATURE" in captured.err or "unresolved" in captured.err.lower()


def test_preprocess_cli_compact_is_explicit(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text("#if 0\ndead\n#endif\n\nkept\n", encoding="utf-8")
    assert main(["preprocess", str(source)]) == 0
    canonical = capsys.readouterr().out
    assert "kept" in canonical
    assert "dead" not in canonical
    assert canonical.count("\n") == 5
    assert main(["preprocess", str(source), "--compact"]) == 0
    assert capsys.readouterr().out == "kept\n"


def test_preprocess_cli_compact_rejects_negative_limit(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text("#if 0\ndead\n#endif\nkept\n", encoding="utf-8")
    assert main(["preprocess", str(source), "--compact", "--max-blank-lines", "-1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "non-negative" in captured.err


def test_preprocess_cli_rejects_batch_inputs(tmp_path, capsys):
    first = tmp_path / "one.c"
    second = tmp_path / "two.c"
    first.write_text("int one;\n", encoding="utf-8")
    second.write_text("int two;\n", encoding="utf-8")
    assert main(["preprocess", str(first), str(second)]) == 2
    assert "exactly one source file" in capsys.readouterr().err


def test_preprocess_cli_help_describes_concrete_workflow(capsys):
    try:
        main(["preprocess", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "concrete preprocessing" in output.lower()
    assert "--define" in output
    assert "--compact" in output
