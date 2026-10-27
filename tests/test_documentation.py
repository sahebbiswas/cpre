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


# --- Rollout guides stay executable and complete -------------------------------------------

import contextlib  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import cpre  # noqa: E402
from cpre.cli import main  # noqa: E402

DOCS = ROOT / "docs"
GUIDES = [DOCS / "getting-started.md", DOCS / "cookbook.md", DOCS / "api.md"]
FENCE_RE = re.compile(r"```(\w+)\n(.*?)```", re.DOTALL)
SAMPLE_RE = re.compile(r"`(include/config\.h|src/driver\.c)`:\n\n```c\n(.*?)```", re.DOTALL)


def _fences(document: Path, language: str) -> list[str]:
    text = document.read_text(encoding="utf-8")
    return [body for lang, body in FENCE_RE.findall(text) if lang == language]


@pytest.fixture
def sample_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The sample project exactly as the Getting started guide prints it."""
    guide = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    files = dict(SAMPLE_RE.findall(guide))
    assert set(files) == {"include/config.h", "src/driver.c"}
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _Utf8Text(io.StringIO):
    """A captured stream that, like a UTF-8 terminal, accepts the report's Unicode glyphs."""

    encoding = "utf-8"  # type: ignore[assignment]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = _Utf8Text(), _Utf8Text()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    # Documentation shows POSIX paths; Windows prints the same paths with backslashes.
    return code, out.getvalue().replace("\\", "/"), err.getvalue().replace("\\", "/")


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_python_examples_in_guides_run(guide: Path, sample_project: Path) -> None:
    blocks = _fences(guide, "python")
    assert blocks, f"{guide.name} has no runnable Python examples"
    if guide.name == "api.md":
        # Only the "Typical setups" section is self-contained against the sample project.
        text = guide.read_text(encoding="utf-8")
        section = text.split("## Typical setups", 1)[1].split("## Symbolic expression API", 1)[0]
        blocks = [body for lang, body in FENCE_RE.findall(section) if lang == "python"]
        assert len(blocks) >= 6
    for number, block in enumerate(blocks, 1):
        try:
            exec(compile(block, f"{guide.name} block {number}", "exec"), {})  # noqa: S102
        except Exception as error:  # pragma: no cover - failure path
            pytest.fail(f"{guide.name} Python block {number} failed: {error!r}\n{block}")


def test_getting_started_command_output_matches_reality(sample_project: Path) -> None:
    guide = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    texts = _fences(DOCS / "getting-started.md", "text")

    code, out, _ = _run(["src/driver.c"])
    assert code == 0
    assert out in texts, "the Step 1 report in getting-started.md is out of date"

    code, out, _ = _run(["preprocess", "src/driver.c", "--list-unknown-macros"])
    assert code == 0
    assert out in texts, "the --list-unknown-macros listing in getting-started.md is out of date"

    code, _, err = _run(["preprocess", "src/driver.c", "-D", "DEBUG"])
    assert code == 2
    for line in err.splitlines():
        assert line in guide, f"stderr line missing from getting-started.md: {line}"


def test_cookbook_cover_listing_matches_reality(sample_project: Path) -> None:
    code, out, _ = _run(["cover", "src/driver.c", "--skip-includes"])
    assert code == 0
    cookbook = (DOCS / "cookbook.md").read_text(encoding="utf-8")
    for line in out.splitlines():
        if not line.startswith("# unsatisfiable"):
            assert line in cookbook, f"cover line missing from cookbook.md: {line}"


def test_api_index_names_every_public_symbol() -> None:
    text = (DOCS / "api.md").read_text(encoding="utf-8")
    index = text.split("## Public API index", 1)[1].split("## Typical setups", 1)[0]
    listed = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", index))
    missing = sorted(set(cpre.__all__) - listed)
    assert not missing, f"public names missing from the API index: {missing}"


def test_every_cli_option_is_documented() -> None:
    cli = (DOCS / "cli.md").read_text(encoding="utf-8")
    option_re = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")
    for command in (
        "",
        "simplify-macros",
        "analyze-macros",
        "preprocess",
        "cover",
        "evaluate",
        "test-input",
    ):
        argv = [sys.executable, "-m", "cpre", *([command] if command else []), "--help"]
        env = {**os.environ, "COLUMNS": "200"}
        help_text = subprocess.run(argv, capture_output=True, text=True, env=env, check=True).stdout
        undocumented = sorted({o for o in option_re.findall(help_text) if o not in cli})
        assert not undocumented, f"`cpre {command}` options missing from cli.md: {undocumented}"


def test_troubleshooting_quotes_current_messages(sample_project: Path) -> None:
    guide = (DOCS / "troubleshooting.md").read_text(encoding="utf-8")
    _, _, err = _run(["preprocess", "src/driver.c", "-D", "DEBUG"])
    for line in err.splitlines():
        assert line in guide, f"stderr line missing from troubleshooting.md: {line}"
    _, _, err = _run(["preprocess", "src/driver.c", "-I", "include"])
    assert "condition is not determined by the current macro state" in err
    assert "condition is not determined by the current macro state" in guide


def test_cookbook_header_recipe_matches_reality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookbook = (DOCS / "cookbook.md").read_text(encoding="utf-8")
    header = re.search(r"```c\n(#pragma once\n#ifndef WIDGET_H.*?)```", cookbook, re.DOTALL)
    assert header is not None
    (tmp_path / "widget.h").write_bytes(header.group(1).encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    code, _, err = _run(["preprocess", "widget.h", "-D", "FEATURE"])
    assert code == 2 and "#ifndef WIDGET_H (unresolved: WIDGET_H (definedness))" in err
    code, _, err = _run(["preprocess", "widget.h", "-U", "WIDGET_H", "-D", "FEATURE"])
    assert code == 2 and "#pragma preprocessing directive is not supported" in err
    done = subprocess.run(
        [sys.executable, "-m", "cpre", "preprocess", "widget.h", "--consume-pragma", "once"]
        + ["-U", "WIDGET_H", "-D", "FEATURE", "--compact"],
        capture_output=True,
        text=True,
    )
    assert (done.returncode, done.stdout.strip()) == (0, "int f(void);")
    code, out, _ = _run(["cover", "widget.h", "--consume-pragma", "once"])
    assert code == 0 and "-D WIDGET_H=1" in out
