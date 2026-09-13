from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _markdown_links(document: Path) -> list[str]:
    return LINK_RE.findall(document.read_text(encoding="utf-8"))


def _is_external_or_anchor(target: str) -> bool:
    return target.startswith(("https://", "http://", "mailto:", "#"))


DOCUMENTS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: str(path.relative_to(ROOT)))
def test_relative_markdown_links_resolve(document: Path) -> None:
    for target in _markdown_links(document):
        if _is_external_or_anchor(target):
            continue
        relative = target.split("#", 1)[0].split("?", 1)[0]
        if not relative:
            continue
        resolved = (document.parent / relative).resolve()
        assert resolved.exists(), f"broken link in {document.relative_to(ROOT)}: {target}"


def test_readme_cross_file_links_are_pypi_safe() -> None:
    for target in _markdown_links(ROOT / "README.md"):
        assert _is_external_or_anchor(target), (
            "README cross-file links must be absolute so the PyPI long description "
            f"does not resolve them relative to pypi.org: {target}"
        )
