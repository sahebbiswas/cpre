"""The command line is a package: ``main`` dispatches, commands are separate modules,
and the JSON and text builders can be used without importing any command."""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

import cpre.cli
from cpre.cli import _common

PACKAGE = Path(cpre.cli.__file__).resolve().parent
ROOT = PACKAGE.parent.parent
MODULES = sorted(PACKAGE.glob("*.py"))


def test_cli_is_a_package_with_one_module_per_command():
    names = {p.stem for p in MODULES}
    assert {
        "__init__",
        "_common",
        "analyze",
        "simplify_macros",
        "preprocess",
        "boolean_expression",
    } <= names


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_cli_module_is_longer_than_400_lines(path):
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 400


@pytest.mark.parametrize("module", ["cpre.serialization", "cpre.text_reports"])
def test_builders_import_without_any_command_module(module):
    probe = (
        "import sys, importlib\n"
        f"importlib.import_module({module!r})\n"
        "loaded = sorted(m for m in sys.modules if m == 'cpre.cli' or m.startswith('cpre.cli.'))\n"
        "print(loaded)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,  # puts the source tree first on the path, with the environment untouched
        check=True,
    )
    assert result.stdout.strip() == "[]"


def test_mypy_no_longer_ignores_the_cli():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"cpre.cli",' not in text


@pytest.mark.parametrize(
    "argv, command, remaining, prog",
    [
        (["simplify-macros", "a.c"], "simplify_macros_main", ["a.c"], "cpre simplify-macros"),
        (["analyze-macros", "a.c"], "simplify_macros_main", ["a.c"], "cpre analyze-macros"),
        (["preprocess", "a.c"], "preprocess_main", ["a.c"], "cpre preprocess"),
        (["test-input", "A"], "test_input_main", ["A"], "cpre test-input"),
    ],
)
def test_main_dispatches_by_first_argument(monkeypatch, argv, command, remaining, prog):
    calls = []
    monkeypatch.setattr(cpre.cli, command, lambda args, prog: calls.append((args, prog)) or 7)
    assert cpre.cli.main(argv) == 7
    assert calls == [(remaining, prog)]


def test_main_falls_through_to_the_default_analysis(monkeypatch):
    seen = []
    monkeypatch.setattr(cpre.cli, "analyze_main", lambda argv: seen.append(argv) or 3)
    assert cpre.cli.main(["--json", "a.c"]) == 3
    assert seen == [["--json", "a.c"]]


def test_main_reads_sys_argv_when_called_without_arguments(monkeypatch):
    seen = []
    monkeypatch.setattr(cpre.cli, "analyze_main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["cpre", "x.c"])
    cpre.cli.main()
    assert seen == [["x.c"]]


def test_exit_statuses():
    assert (_common.EXIT_OK, _common.EXIT_FINDINGS, _common.EXIT_ERROR) == (0, 1, 2)


def _parser():
    parser = argparse.ArgumentParser(prog="t")
    _common.add_symbolic_literal_options(parser, "macro")
    return parser


def test_symbolic_literals_are_collected_and_validated():
    parser = _parser()
    args = parser.parse_args(["--symbolic-zero", "--symbolic-literal", "0"])
    assert _common.symbolic_literals_from(parser, args) == (0,)
    assert _common.symbolic_literals_from(parser, parser.parse_args([])) == ()


def test_invalid_symbolic_literal_is_a_usage_error(capsys):
    parser = _parser()
    args = parser.parse_args(["--symbolic-literal", "1"])
    with pytest.raises(SystemExit) as raised:
        _common.symbolic_literals_from(parser, args)
    assert raised.value.code == 2
    assert "t: error:" in capsys.readouterr().err


def test_symbolic_literal_help_names_the_analysis():
    assert "in expression analysis" in _help("expression")
    assert "in macro analysis" in _help("macro")


def _help(analysis):
    parser = argparse.ArgumentParser(prog="t")
    _common.add_symbolic_literal_options(parser, analysis)
    return " ".join(parser.format_help().split())


def test_batch_mode_is_several_inputs_or_a_directory(tmp_path):
    one = argparse.Namespace(sources=[tmp_path / "a.c"])
    assert not _common.is_batch(one)
    assert _common.is_batch(argparse.Namespace(sources=[tmp_path / "a.c", tmp_path / "b.c"]))
    assert _common.is_batch(argparse.Namespace(sources=[tmp_path]))


def test_write_exact_keeps_carriage_returns(capsysbinary):
    _common.write_exact("a\r\nb\r\n")
    assert capsysbinary.readouterr().out == b"a\r\nb\r\n"


def test_write_exact_falls_back_to_text_streams(monkeypatch):
    class TextOnly:
        def __init__(self):
            self.parts = []

        def write(self, text):
            self.parts.append(text)

    stream = TextOnly()
    monkeypatch.setattr(sys, "stdout", stream)
    _common.write_exact("x\n")
    assert stream.parts == ["x\n"]


def test_report_failure_prefixes_the_path(capsys):
    _common.report_failure(Path("f.c"), "boom")
    assert capsys.readouterr().err == "f.c: boom\n"
