from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check_version_bump.py"


def _run(tmp_path: Path, old: str, new: str) -> subprocess.CompletedProcess[str]:
    old_file = tmp_path / "old.py"
    new_file = tmp_path / "new.py"
    old_file.write_text(old, encoding="utf-8")
    new_file.write_text(new, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(old_file), str(new_file)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("new", "kind"),
    [("0.20.1", "patch"), ("0.21.0", "minor"), ("1.0.0", "major")],
)
def test_one_step_bumps_pass(tmp_path: Path, new: str, kind: str) -> None:
    result = _run(tmp_path, '__version__ = "0.20.0"\n', f'__version__ = "{new}"\n')
    assert result.returncode == 0, result.stderr
    assert f"({kind} bump)" in result.stdout


@pytest.mark.parametrize("new", ["0.20.0", "0.20.2", "0.21.1", "0.19.0", "2.0.0"])
def test_missing_or_oversized_bumps_fail(tmp_path: Path, new: str) -> None:
    result = _run(tmp_path, '__version__ = "0.20.0"\n', f'__version__ = "{new}"\n')
    assert result.returncode == 1
    assert "cpre/__init__.py" in result.stderr


@pytest.mark.parametrize(
    "new",
    [
        '__version__ = "0.20.1rc1"\n',
        "__version__ = VERSION\n",
        '__version__ = "0.20.1"\n__version__ = "0.20.2"\n',
        "VERSION = 1\n",
    ],
)
def test_malformed_version_files_fail(tmp_path: Path, new: str) -> None:
    result = _run(tmp_path, '__version__ = "0.20.0"\n', new)
    assert result.returncode != 0
    assert "error:" in result.stderr


def test_package_version_is_read_from_cpre_init() -> None:
    spec = importlib.util.spec_from_file_location("check_version_bump", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import cpre

    assert module.read_version(str(ROOT / "cpre" / "__init__.py")) == cpre.__version__
