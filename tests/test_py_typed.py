"""cpre ships its type information to downstream type checkers (PEP 561)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cpre


def test_the_typing_marker_sits_next_to_the_package():
    assert (Path(cpre.__file__).parent / "py.typed").is_file()


def test_the_marker_is_declared_as_package_data_and_classified(pyproject: dict[str, Any]):
    assert "py.typed" in pyproject["tool"]["setuptools"]["package-data"]["cpre"]
    assert "Typing :: Typed" in pyproject["project"]["classifiers"]
