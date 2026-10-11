"""Tests for the top-level CLI version option."""

from __future__ import annotations

import subprocess
import sys

import pytest

from cpre import __version__
from cpre.cli import main


def test_main_version_prints_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"cpre {__version__}"


@pytest.mark.parametrize("args", [["--version"], ["-m", "cpre", "--version"]])
def test_python_entrypoint_version(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"cpre {__version__}"
    assert result.stderr == ""
