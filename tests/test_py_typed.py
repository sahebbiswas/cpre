"""cpre ships its type information to downstream type checkers (PEP 561)."""

from __future__ import annotations

import re
from pathlib import Path

import cpre

ROOT = Path(__file__).resolve().parent.parent


def test_the_typing_marker_sits_next_to_the_package():
    assert (Path(cpre.__file__).parent / "py.typed").is_file()


def test_the_marker_is_declared_as_package_data_and_classified():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'\[tool\.setuptools\.package-data\]\s+cpre = \[[^\]]*"py\.typed"', config)
    assert '"Typing :: Typed"' in config
