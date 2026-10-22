"""The changelog and security policy exist, are linked, and follow their stated format."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _package_version() -> str:
    """``__version__`` read from the source, so the test does not import the package."""
    init = (ROOT / "cpre" / "__init__.py").read_text(encoding="utf-8")
    # A release dry run sets a pre-release such as 0.42.3rc1 (CONTRIBUTING.md, "Releasing").
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)(?:rc\d+)?"$', init, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_changelog_has_an_unreleased_section_above_the_releases():
    headings = re.findall(r"^## \[?([^\]\n]+)\]?", CHANGELOG, re.MULTILINE)
    assert headings[0] == "Unreleased"
    versions = [h.split()[0] for h in headings if re.match(r"\d+\.\d+\.\d+", h)]
    assert versions == sorted(versions, key=lambda v: tuple(map(int, v.split("."))), reverse=True)


def test_the_running_version_is_not_older_than_the_newest_changelog_release():
    newest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.MULTILINE)
    assert newest is not None
    as_tuple = lambda v: tuple(int(part) for part in v.split("."))  # noqa: E731
    assert as_tuple(_package_version()) >= as_tuple(newest.group(1))


def test_every_release_heading_has_a_link_target():
    for version in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.MULTILINE):
        assert re.search(rf"^\[{re.escape(version)}\]: https://", CHANGELOG, re.MULTILINE)
    assert re.search(r"^\[Unreleased\]: https://", CHANGELOG, re.MULTILINE)


def test_security_policy_names_a_private_reporting_channel():
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security/advisories/new" in policy
    assert "Supported versions" in policy


def test_readme_contributing_and_json_contract_point_to_the_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "CHANGELOG.md" in readme and "SECURITY.md" in readme
    assert "CHANGELOG.md" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "../CHANGELOG.md" in (ROOT / "docs/json-contract.md").read_text(encoding="utf-8")


def test_the_version_reader_accepts_a_release_candidate(monkeypatch, tmp_path):
    """The release dry run sets ``0.42.3rc1``; the policy tests must still run then."""
    fake = tmp_path / "cpre"
    fake.mkdir()
    (fake / "__init__.py").write_text('__version__ = "0.42.3rc1"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _package_version() == "0.42.3"
