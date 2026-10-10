from __future__ import annotations

from pathlib import Path

import pytest

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
    assert "--config-from-unknown-names undefined" in captured.err
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


@pytest.mark.parametrize("directive", ["#define __STDC__ 0", "#undef __STDC__"])
def test_config_from_seed_changing_context_macro_names_the_seed(tmp_path, capsys, directive):
    """A seed that changes a --standard-macro name is reported against the seed."""
    seed = _write(tmp_path / "s.h", f"#define FEATURE 1\n{directive}\n")
    target = _write(tmp_path / "t.c", "int x = __STDC__;\n")
    argv = ["preprocess", "--standard-macro", "__STDC__=1", "--config-from", str(seed)]
    assert main([*argv, str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{seed}: invalid --config-from seed: line 2: seed " in captured.err
    assert "__STDC__, which the preprocessing context supplies" in captured.err
    assert str(target) not in captured.err


# The stripped FLAGS_H guard stays unknown in an open-world target, so these
# targets test only names the seed settles.
OPEN_TARGET = TARGET.replace("#ifdef FLAGS_H\nint guarded;\n#endif\n", "")


def _seed_for_open_target(flags: Path) -> cpre.MacroConfiguration:
    """The API equivalent: closed-world seed, open-world target."""
    seeded = cpre.MacroConfiguration.from_source(
        flags.read_text(encoding="utf-8"), filename=str(flags), unknown_names="undefined"
    )
    return cpre.MacroConfiguration(
        definitions=seeded.definitions, undefined=seeded.undefined, unknown_names="open"
    )


def test_config_from_closed_seed_with_open_target(tmp_path, capsys):
    """A guarded seed is read closed-world while the target stays open-world."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", OPEN_TARGET)
    argv = ["preprocess", "--config-from-unknown-names", "undefined", "--config-from"]
    assert main([*argv, str(flags), str(target)]) == 0
    configuration = _seed_for_open_target(flags)
    assert configuration.unknown_names is cpre.UnknownNamePolicy.OPEN
    assert capsys.readouterr().out == _expected(target, configuration)


def test_config_from_seed_policy_does_not_close_the_target(tmp_path, capsys):
    """Names neither the seed nor -D/-U settle stay unknown in an open-world target."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", OPEN_TARGET + "#ifdef UNSET\nint unset;\n#endif\n")
    argv = ["preprocess", "--config-from-unknown-names", "undefined", "--config-from", str(flags)]
    assert main([*argv, "--list-unknown-macros", str(target)]) == 0
    assert capsys.readouterr().out.split("\t")[0] == "UNSET"
    assert main([*argv, str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{target}: line 9:" in captured.err
    assert "supply -D UNSET or -U UNSET" in captured.err

    # Closing the target as well resolves UNSET as undefined.
    assert main([*argv, "--unknown-names", "undefined", str(target)]) == 0
    assert "int unset;" not in capsys.readouterr().out


def test_config_from_open_seed_with_closed_target(tmp_path, capsys):
    """An explicit open seed policy still rejects an undetermined seed condition."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", TARGET)
    argv = ["preprocess", "--unknown-names", "undefined", "--config-from-unknown-names", "open"]
    assert main([*argv, "--config-from", str(flags), str(target)]) == 2
    captured = capsys.readouterr()
    assert f"{flags}: invalid --config-from seed: line 1:" in captured.err
    assert "hint:" in captured.err


def test_config_from_seed_policy_defaults_to_target_policy(tmp_path, capsys):
    """Without the option the seed uses --unknown-names, as before."""
    flags = _write(tmp_path / "flags.h", FLAGS)
    target = _write(tmp_path / "target.c", TARGET)
    closed = ["preprocess", "--unknown-names", "undefined", "--config-from", str(flags)]
    assert main([*closed, str(target)]) == 0
    default_output = capsys.readouterr().out
    assert main([*closed, "--config-from-unknown-names", "undefined", str(target)]) == 0
    assert capsys.readouterr().out == default_output


def test_config_from_unknown_names_requires_config_from(tmp_path, capsys):
    target = _write(tmp_path / "target.c", "int x;\n")
    with pytest.raises(SystemExit) as excinfo:
        main(["preprocess", "--config-from-unknown-names", "undefined", str(target)])
    assert excinfo.value.code == 2
    assert "--config-from-unknown-names requires --config-from" in capsys.readouterr().err


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
    assert "--config-from-unknown-names" in out
    assert "cpre preprocess --unknown-names undefined --config-from flags.h" in " ".join(
        out.split()
    )
