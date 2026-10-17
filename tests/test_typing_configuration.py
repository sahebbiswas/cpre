"""Every cpre module is type checked under the normal mypy configuration (#99)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "cpre").rglob("*.py"))


def test_no_module_is_exempt_from_mypy():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "ignore_errors" not in config
    assert "[[tool.mypy.overrides]]" not in config


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_blanket_suppression_in_source(path):
    text = path.read_text(encoding="utf-8")
    assert "# type: ignore" not in text
    assert "# mypy: ignore-errors" not in text
