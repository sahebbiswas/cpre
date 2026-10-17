"""One core entry point: public functions are adapters that build a request (issue #164)."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import cpre
from cpre import include_queries, pragmas, preprocessing

PACKAGE = Path(__file__).resolve().parent.parent / "cpre"

PROBE = r"""
import importlib, inspect, sys
sys.path.insert(0, sys.argv[2])
first = importlib.import_module(sys.argv[1])
fn = first.preprocess_source
source = "#if __has_include(<a.h>) && !__has_include(<b.h>)\nyes\n#else\nno\n#endif\n"
result = fn(source, include_query=lambda q: q.header == "a.h")
import cpre.preprocessing, cpre.include_queries
print(fn.__module__, cpre.preprocessing.preprocess_source is cpre.include_queries.preprocess_source)
print(list(inspect.signature(cpre.preprocessing.preprocess_source).parameters))
print(result.complete, repr(result.source))
"""


def _probe(first: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, first, str(PACKAGE.parent)],
        capture_output=True,
        text=True,
        check=True,
        cwd=PACKAGE.parent,
    )
    return done.stdout.splitlines()


def test_import_order_does_not_change_preprocess_source():
    outputs = {name: _probe(name) for name in ("cpre.preprocessing", "cpre.include_queries")}
    outputs["cpre.pragmas"] = _probe("cpre.pragmas")
    # ``cpre.pragmas.preprocess_source`` additionally takes ``pragma_handler``; the other
    # two are the same function whichever module was imported first.
    assert outputs["cpre.preprocessing"] == outputs["cpre.include_queries"]
    assert outputs["cpre.preprocessing"][0].startswith("cpre.preprocessing True")
    assert outputs["cpre.pragmas"][1:] == outputs["cpre.preprocessing"][1:]
    assert outputs["cpre.preprocessing"][2].startswith("True ")


def test_include_queries_and_preprocessing_share_one_function():
    assert include_queries.preprocess_source is preprocessing.preprocess_source
    assert cpre.preprocess_source is pragmas.preprocess_source
    assert pragmas.preprocess_source is not preprocessing.preprocess_source


@pytest.mark.parametrize(
    "function",
    [
        preprocessing.preprocess_source,
        preprocessing.preprocess_request,
        include_queries.preprocess_source,
        pragmas.preprocess_source,
    ],
    ids=lambda f: f"{f.__module__}.{f.__name__}",
)
def test_entry_points_take_no_underscore_parameters(function):
    names = list(inspect.signature(function).parameters)
    assert not [name for name in names if name.startswith("_")]


def test_request_fields_are_public():
    fields = preprocessing.PreprocessRequest.__dataclass_fields__
    assert not [name for name in fields if name.startswith("_")]


def test_documented_signature_is_unchanged():
    assert list(inspect.signature(cpre.preprocess_source).parameters) == [
        "source",
        "filename",
        "assumptions",
        "configuration",
        "context",
        "include_query",
        "pragma_handler",
        "options",
        "skip_includes",
        "include_resolver",
        "max_include_depth",
        "has_include_from_resolver",
    ]
    assert list(inspect.signature(preprocessing.preprocess_source).parameters) == [
        "source",
        "filename",
        "assumptions",
        "configuration",
        "context",
        "include_query",
        "options",
        "skip_includes",
        "include_resolver",
        "max_include_depth",
        "has_include_from_resolver",
    ]


def _module_attribute_assignments(path: Path) -> list[str]:
    """``something.attr = ...`` statements at module level (rebinding another module)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        found.extend(
            f"{path.name}:{node.lineno}" for target in targets if isinstance(target, ast.Attribute)
        )
    return found


def test_no_module_rebinds_an_attribute_of_another_module():
    offenders = [
        hit for path in PACKAGE.glob("*.py") for hit in _module_attribute_assignments(path)
    ]
    assert offenders == []


def test_no_type_ignore_assignment_remains():
    for path in PACKAGE.glob("*.py"):
        assert "type: ignore[assignment]" not in path.read_text(encoding="utf-8"), path.name
