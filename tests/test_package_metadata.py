"""Package metadata follows current packaging standards (PEP 639 license expression)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


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


def test_the_python_floor_matches_classifiers_ci_and_tox(pyproject: dict[str, Any]):
    floor = re.fullmatch(r">=(3\.\d+)", pyproject["project"]["requires-python"])
    assert floor is not None
    minors = [
        match.group(1)
        for classifier in pyproject["project"]["classifiers"]
        if (match := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", classifier))
    ]
    assert minors[0] == floor.group(1)
    assert minors == sorted(minors, key=lambda v: int(v.split(".")[1]))

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version: \[([^\]]+)\]", ci)
    assert matrix is not None
    assert re.findall(r'"(3\.\d+)"', matrix.group(1)) == minors

    tox = (ROOT / "tox.ini").read_text(encoding="utf-8")
    envs = re.search(r"envlist = (.+)", tox)
    assert envs is not None
    assert [e for e in envs.group(1).split(", ") if e.startswith("py")] == [
        "py" + m.replace(".", "") for m in minors
    ]
    assert pyproject["tool"]["ruff"]["target-version"] == "py" + floor.group(1).replace(".", "")


def test_development_status_matches_the_major_version(pyproject: dict[str, Any]):
    major = int(_package_version().split(".")[0])
    (status,) = [
        c for c in pyproject["project"]["classifiers"] if c.startswith("Development Status ::")
    ]
    if major >= 1:
        assert status == "Development Status :: 5 - Production/Stable"
    else:
        assert status in {
            "Development Status :: 3 - Alpha",
            "Development Status :: 4 - Beta",
        }


def _package_version() -> str:
    init = (ROOT / "cpre" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+)\.\d+\.\d+"$', init, re.MULTILINE)
    assert match is not None
    return match.group(0).split('"')[1]


def test_the_stability_promise_is_documented_where_users_look():
    promise = (ROOT / "docs/downstream-compatibility.md").read_text(encoding="utf-8")
    assert "## What 1.0 promises" in promise
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Beta" not in readme
    assert "downstream-compatibility.md#what-10-promises" in readme
