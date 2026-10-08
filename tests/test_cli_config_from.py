from __future__ import annotations

from pathlib import Path

import cpre
from cpre.cli import main

FLAGS = (
    "#ifndef FLAGS_H\n"
    "#define FLAGS_H\n"
    "#define FEATURE\n"
    "#ifndef LEVEL\n"
    "#define LEVEL 1\n"
    "#endif\n"
    "#define SQUARE(x) ((x) * (x))\n"
    "#undef LEGACY\n"
    "#endif\n"
)

TARGET = (
    "#ifdef FLAGS_H\nint guarded;\n#endif\n"
    "#ifdef FEATURE\nint feature;\n#endif\n"
    "#ifdef LEGACY\nint legacy;\n#endif\n"
    "int level = LEVEL;\n"
    "int area = SQUARE(3);\n"
)


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def _expected(target: Path, configuration: cpre.MacroConfiguration) -> str:
    result = cpre.preprocess_source(
        target.read_text(encoding="utf-8"), filename=str(target), configuration=configuration
    )
    assert result.complete
    assert result.source is not None
    return result.source


def test_config_from_matches_api_from_source(tmp_path, capsys):
    """A seed file yields the same macro state as MacroConfiguration.from_source()."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", TARGET)
    configuration = cpre.MacroConfiguration.from_source(
        FLAGS, filename=str(flags), unknown_names="undefined"
    )
    assert "FLAGS_H" not in {d.name for d in configuration.definitions}

    argv = ["preprocess", "--unknown-names", "undefined", "--config-from", str(flags), str(target)]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert out == _expected(target, configuration)
    assert "int guarded;" not in out  # include guard stripped, as in the API
    assert "int feature;" in out
    assert "int legacy;" not in out
    assert "( ( 3 ) * ( 3 ) )" in out


def test_config_from_explicit_options_take_precedence(tmp_path, capsys):
    """-D/-U are visible to seed conditions and override seed definitions."""
    flags = _write(
        tmp_path / "flags.h", FLAGS.replace("#undef LEGACY\n", "#undef LEGACY\n#define FORCED 7\n")
    )
    target = _write(tmp_path / "target.c", TARGET + "int forced = FORCED;\n")
    argv = [
        "preprocess",
        "--unknown-names",
        "undefined",
        "--config-from",
        str(flags),
        "-DLEVEL=2",
        "-DFORCED=9",
        "-UFEATURE",
        "-DLEGACY",
        str(target),
    ]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "int guarded;" not in out
    assert "int level =  2 ;" in out
    assert "int forced =  9 ;" in out
    assert "int feature;" not in out
    assert "int legacy;" in out


def test_config_from_explicit_options_are_seed_base(tmp_path, capsys):
    """Seed #ifndef defaults see command-line definitions."""
    flags = _write(
        tmp_path / "flags.h", "#ifndef LEVEL\n#define LEVEL 1\n#endif\n#define SEEN LEVEL\n"
    )
    target = _write(tmp_path / "target.c", "int seen = SEEN;\n")
    assert main(["preprocess", "--config-from", str(flags), "-DLEVEL=5", str(target)]) == 0
    assert "5" in capsys.readouterr().out


def test_config_from_multiple_seeds_apply_in_order(tmp_path, capsys):
    """Later seeds layer on earlier ones with last-definition-wins semantics."""
    first = _write(tmp_path / "first.h", "#define A 1\n#define B 1\n#define C\n")
    second = _write(tmp_path / "second.h", "#undef A\n#define B 2\n")
    target = _write(
        tmp_path / "target.c", "#ifdef A\nint a;\n#endif\nint b = B;\n#ifdef C\nint c;\n#endif\n"
    )
    base = cpre.MacroConfiguration.from_source(first.read_text(), filename=str(first))
    layered = cpre.MacroConfiguration.from_source(
        second.read_text(), filename=str(second), base=base
    )
    argv = ["preprocess", "--config-from", str(first), "--config-from", str(second), str(target)]
    assert main(argv) == 0
    assert capsys.readouterr().out == _expected(target, layered)


def test_config_from_without_option_is_unchanged(tmp_path, capsys):
    """Existing explicit -D/-U behavior is unaffected when no seed is given."""
    target = _write(tmp_path / "target.c", "#ifdef FEATURE\nint feature;\n#endif\n")
    assert main(["preprocess", "-DFEATURE", str(target)]) == 0
    assert capsys.readouterr().out == _expected(
        target, cpre.MacroConfiguration(presence=["FEATURE"])
    )


def test_config_from_open_world_guard_reports_hint(tmp_path, capsys):
    """An open-world include guard fails with a location and an actionable hint."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", TARGET)
    assert main(["preprocess", "--config-from", str(flags), str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{flags}: invalid --config-from seed: line 1:" in captured.err
    assert "--unknown-names undefined" in captured.err


def test_config_from_invalid_seed_syntax(tmp_path, capsys):
    """A malformed seed reports its path and source location."""
    flags = _write(tmp_path / "flags.h", "#if FOO(\n#endif\n")
    target = _write(tmp_path / "target.c", "int x;\n")
    assert main(["preprocess", "--config-from", str(flags), str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{flags}: invalid --config-from seed:" in captured.err
    assert "line 1" in captured.err


def test_config_from_seed_with_include_is_rejected(tmp_path, capsys):
    """Seeds with active #include directives fail rather than reading headers."""
    flags = _write(tmp_path / "flags.h", '#include "other.h"\n')
    target = _write(tmp_path / "target.c", "int x;\n")
    assert main(["preprocess", "--config-from", str(flags), str(target)]) == 2
    assert f"{flags}: invalid --config-from seed: line 1:" in capsys.readouterr().err


def test_config_from_missing_or_directory_seed(tmp_path, capsys):
    """Unreadable seeds fail with exit status 2 and name the seed path."""
    target = _write(tmp_path / "target.c", "int x;\n")
    missing = tmp_path / "missing.h"
    assert main(["preprocess", "--config-from", str(missing), str(target)]) == 2
    assert f"{missing}: cannot read --config-from seed" in capsys.readouterr().err

    assert main(["preprocess", "--config-from", str(tmp_path), str(target)]) == 2
    assert "requires a file, not a directory" in capsys.readouterr().err


def test_config_from_help_includes_example(capsys):
    """CLI help documents the option with a concrete example."""
    try:
        main(["preprocess", "--help"])
    except SystemExit as exit_:
        assert exit_.code == 0
    out = capsys.readouterr().out
    assert "--config-from PATH" in out
    assert "cpre preprocess --config-from flags.h" in out
