"""The public facade sits above the core: nothing below ``cli`` imports ``cpre.api`` or ``cpre.cpre``.

``cpre.api`` is the stable entry point for library consumers and ``cpre.cpre`` is the internal
compatibility facade kept for script mode and historical callers. The core modules read the
option and result types from ``cpre._types`` instead, so a change to the public module cannot
ripple into the core and the core never has to defer an import to dodge a cycle through it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "cpre"

# The only modules allowed to import the facades: the package root re-exports the public API,
# the facades themselves, and the command line front end that sits on top of everything.
ABOVE_THE_CORE = {"__init__", "__main__", "api", "cpre", "cli"}
FACADES = {"api", "cpre"}


def _imported_facades(path: Path) -> set[str]:
    """Facade modules (``api``/``cpre``) of this package that ``path`` imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:  # ``from .api import x`` / ``from . import api``
                if node.module in FACADES:
                    found.add(node.module)
                elif node.module is None:
                    found.update(a.name for a in node.names if a.name in FACADES)
            elif node.level == 0 and node.module:  # ``from cpre.api import x``
                parts = node.module.split(".")
                if parts[0] == "cpre" and len(parts) > 1 and parts[1] in FACADES:
                    found.add(parts[1])
                elif node.module == "cpre":
                    found.update(a.name for a in node.names if a.name in FACADES)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "cpre" and len(parts) > 1 and parts[1] in FACADES:
                    found.add(parts[1])
    return found


CORE = sorted(p for p in PACKAGE.glob("*.py") if p.stem not in ABOVE_THE_CORE)


def test_the_core_is_not_empty():
    assert {p.stem for p in CORE} >= {"preprocessing", "configuration", "lexer", "_types"}


@pytest.mark.parametrize("path", CORE, ids=lambda p: p.stem)
def test_core_module_does_not_import_a_facade(path):
    assert _imported_facades(path) == set(), f"{path.name} must not import cpre.api or cpre.cpre"


def test_api_does_not_import_the_compatibility_facade():
    assert "cpre" not in _imported_facades(PACKAGE / "api.py")


def test_detector_sees_every_import_form(tmp_path):
    forms = {
        "from .api import X": {"api"},
        "from . import cpre": {"cpre"},
        "from cpre.api import X": {"api"},
        "from cpre import cpre as engine": {"cpre"},
        "import cpre.cpre": {"cpre"},
        "from .errors import X": set(),
    }
    for text, expected in forms.items():
        module = tmp_path / "m.py"
        module.write_text(text + "\n")
        assert _imported_facades(module) == expected, text


def test_public_types_keep_their_documented_module():
    import cpre
    from cpre import api

    for name in ("AnalysisOptions", "AnalysisResult", "Finding", "MacroAssumptions"):
        assert getattr(cpre, name) is getattr(api, name)
        assert getattr(api, name).__module__ == "cpre.api"
