"""``cpre preprocess --dump-macros`` shows the configured macro environment (#101)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cpre.cli import main
from cpre.configuration import MacroConfiguration
from cpre.macro_environment import macro_environment
from cpre.macros import MacroDefinition
from cpre.preprocessing import PreprocessingContext


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))
    return str(path)


@pytest.fixture
def files(tmp_path):
    return {
        "target": _write(tmp_path / "t.c", "#define OWN 1\nint x;\n"),
        "first": _write(
            tmp_path / "first.h",
            "#define LEVEL 1\n#define SHARED first\n#define ADD(a,b) ((a)+(b))\n"
            "#define HELPER_ 1\n",
        ),
        "second": _write(tmp_path / "second.h", "#undef LEVEL\n#define SHARED second\n"),
    }


def _dump(capsys, files, *flags, as_json=False):
    argv = ["preprocess", "--dump-macros", *flags, files["target"]]
    if as_json:
        argv.insert(2, "--json")
    code = main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if as_json else out.splitlines())


def test_text_records_are_sorted_with_state_origin_and_replacement(capsys, files):
    code, lines = _dump(
        capsys,
        files,
        "--config-from", files["first"],
        "-D", "LEVEL=2",
        "-D", "FLAG",
        "-U", "OFF",
        "--standard-macro", "__STDC_VERSION__=201710L",
    )  # fmt: skip
    assert code == 0
    first = files["first"]
    assert lines == [
        f"ADD(a,b)\tdefined\tseed:{first}\t((a)+(b))",
        "FLAG\tdefined\tcommand-line\t",
        f"HELPER_\tdefined\tseed:{first}\t1",
        "LEVEL\tdefined\tcommand-line\t2",
        "OFF\tundefined\tcommand-line\t",
        f"SHARED\tdefined\tseed:{first}\tfirst",
        "__STDC_VERSION__\tdefined\tstandard-macro\t201710L",
    ]


def test_source_defines_are_not_part_of_the_environment(capsys, files):
    _, lines = _dump(capsys, files, "-D", "A")
    assert lines == ["A\tdefined\tcommand-line\t"]


def test_later_seed_redefinition_wins_and_names_its_seed(capsys, files):
    _, data = _dump(
        capsys, files, "--config-from", files["first"], "--config-from", files["second"],
        as_json=True,
    )  # fmt: skip
    by_name = {m["name"]: m for m in data["macros"]}
    assert by_name["SHARED"]["replacement"] == "second"
    assert by_name["SHARED"]["path"] == files["second"]
    assert by_name["LEVEL"]["defined"] is False and by_name["LEVEL"]["path"] == files["second"]
    assert by_name["ADD"]["path"] == files["first"]


def test_command_line_beats_seeds_and_config_exclude_removes_names(capsys, files):
    _, lines = _dump(
        capsys,
        files,
        "--config-from", files["first"],
        "--config-exclude", "HELPER_",
        "--config-exclude", "SHARED",
        "-D", "SHARED=cli",
    )  # fmt: skip
    names = [line.split("\t")[0] for line in lines]
    assert "HELPER_" not in names
    assert "SHARED\tdefined\tcommand-line\tcli" in lines


def test_json_document_fields(capsys, files):
    _, data = _dump(
        capsys, files, "--config-from", files["first"], "-U", "OFF", "--unknown-names", "undefined",
        as_json=True,
    )  # fmt: skip
    assert list(data)[:1] == ["schema"] and data["schema"] == "cpre.macro-environment/1"
    assert set(data) == {"schema", "file", "unknown_names", "macros"}
    assert data["file"] == files["target"] and data["unknown_names"] == "undefined"
    add = next(m for m in data["macros"] if m["name"] == "ADD")
    assert add == {
        "name": "ADD", "defined": True, "replacement": "((a)+(b))", "parameters": ["a", "b"],
        "variadic": False, "origin": "seed", "path": files["first"],
    }  # fmt: skip
    off = next(m for m in data["macros"] if m["name"] == "OFF")
    assert off["defined"] is False and off["replacement"] is None and off["path"] is None


def test_output_is_deterministic_across_hash_seeds(files):
    def run(seed):
        return subprocess.run(
            [
                sys.executable, "-m", "cpre", "preprocess", "--dump-macros", "--json",
                "--config-from", files["first"], "--config-from", files["second"],
                "-U", "Z", "-U", "A", "-D", "M", files["target"],
            ],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout  # fmt: skip

    assert run("0") == run("1") == run("random")


def test_unusual_replacement_text_stays_on_one_record(capsys, files):
    _, lines = _dump(capsys, files, "-D", "TAB=a\tb")
    assert lines == ["TAB\tdefined\tcommand-line\ta\\tb"]


def test_failures_and_conflicts_follow_preprocess_conventions(capsys, files, tmp_path):
    missing = str(tmp_path / "missing.h")
    assert main(["preprocess", "--dump-macros", "--config-from", missing, files["target"]]) == 2
    assert "cannot read --config-from seed" in capsys.readouterr().err
    for other in ("--compact", "--list-unknown-macros"):
        with pytest.raises(SystemExit) as caught:
            main(["preprocess", "--dump-macros", other, files["target"]])
        assert caught.value.code == 2
    capsys.readouterr()


def test_library_entries_default_to_command_line_origin():
    configuration = MacroConfiguration(
        definitions=[MacroDefinition("B", "2")], undefined=["A"], presence=["C"]
    )
    entries = macro_environment(
        configuration, PreprocessingContext(standard_macros={"__STDC__": "1"})
    )
    assert [(e.name, e.origin) for e in entries] == [
        ("A", "command-line"), ("B", "command-line"), ("C", "command-line"),
        ("__STDC__", "standard-macro"),
    ]  # fmt: skip


def test_dumped_environment_reproduces_the_run(capsys, files, tmp_path):
    """Passing the dumped definitions as -D/-U gives the same preprocessing result."""
    _write(tmp_path / "cond.c", "#if LEVEL == 2 && defined(FLAG)\nyes\n#endif\n")
    flags = ["-D", "LEVEL=2", "-D", "FLAG"]
    cond = str(tmp_path / "cond.c")
    assert main(["preprocess", *flags, cond]) == 0
    direct = capsys.readouterr().out
    assert main(["preprocess", "--dump-macros", *flags, cond]) == 0
    dumped = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
    replay = [arg for name, _, _, value in dumped for arg in ("-D", f"{name}={value}")]
    assert main(["preprocess", *replay, cond]) == 0
    assert capsys.readouterr().out == direct


def test_standard_macro_colliding_with_a_configured_name_is_rejected(capsys, files):
    for flag in (["-D", "__STDC__=1"], ["-U", "__STDC__"]):
        code = main(
            [
                "preprocess",
                "--dump-macros",
                *flag,
                "--standard-macro",
                "__STDC__=1",
                files["target"],
            ]
        )
        assert code == 2
        assert "conflicts with configured macro: __STDC__" in capsys.readouterr().err


def test_seed_paths_are_escaped_in_text_records(capsys, tmp_path):
    seed = tmp_path / "se\ted.h"
    try:
        seed.write_bytes(b"#define A 1\n")
    except OSError:  # pragma: no cover - filesystems that reject tabs in names
        pytest.skip("filename with a tab is not supported")
    target = _write(tmp_path / "t.c", "int x;\n")
    assert main(["preprocess", "--dump-macros", "--config-from", str(seed), target]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert len(line.split("\t")) == 4 and "\\t" in line


def test_repeating_a_seed_state_keeps_the_earlier_origin(capsys, tmp_path, files):
    repeat = _write(tmp_path / "repeat.h", "#define SHARED first\n")
    _, data = _dump(
        capsys, files, "--config-from", files["first"], "--config-from", repeat, as_json=True
    )
    shared = next(m for m in data["macros"] if m["name"] == "SHARED")
    assert shared["path"] == files["first"]
