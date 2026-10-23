"""Package metadata follows current packaging standards (PEP 639 license expression)."""

from __future__ import annotations

import re
from typing import Any


def test_license_is_an_spdx_expression_with_a_license_file(pyproject: dict[str, Any]):
    project = pyproject["project"]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]


def test_deprecated_license_forms_are_gone(pyproject: dict[str, Any]):
    project = pyproject["project"]
    assert isinstance(project["license"], str)  # the table form is deprecated
    assert not [c for c in project["classifiers"] if c.startswith("License ::")]


def test_the_build_backend_understands_pep_639(pyproject: dict[str, Any]):
    (requirement,) = [
        r for r in pyproject["build-system"]["requires"] if r.startswith("setuptools")
    ]
    match = re.fullmatch(r"setuptools>=(\d+)(\.\d+)*", requirement)
    assert match is not None and int(match.group(1)) >= 77
