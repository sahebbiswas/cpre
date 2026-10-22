"""Internal C/C++ source path discovery used by the CLI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .model import ConditionError

SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})


STDIN_PATH = Path("<stdin>")
"""Standard input as a source. Its string form is the logical filename every message, JSON
field and ``__FILE__`` uses; the command line spells it ``-``. It is recognized by
*identity* (``path is STDIN_PATH``), never by value, so a real file that happens to be
named ``<stdin>`` stays an ordinary file."""


def source_paths(
    inputs: Sequence[Path],
    recursive: bool,
    on_found: Callable[[int], None] | None = None,
) -> list[Path]:
    """The source files named by ``inputs``, directories expanded when ``recursive``.

    ``on_found`` is called with the running number of source files found, while a
    directory is still being walked, so a caller can show that a large scan is moving.
    """
    paths: list[Path] = []
    seen: set[Path] = set()
    found = 0
    for input_path in inputs:
        if input_path is STDIN_PATH:
            if not any(path is STDIN_PATH for path in paths):
                paths.append(STDIN_PATH)
            continue
        if input_path.is_file():
            candidates = [input_path]
        elif input_path.is_dir():
            if not recursive:
                raise ConditionError(f"{input_path}: is a directory; use --recursive to scan it")
            matched: list[Path] = []
            for entries, path in enumerate(input_path.rglob("*"), 1):
                if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
                    matched.append(path)
                    found += 1
                if on_found is not None and entries % 256 == 0:
                    on_found(found)
            if on_found is not None:
                on_found(found)
            candidates = sorted(matched)
            if not candidates:
                raise ConditionError(f"{input_path}: no C/C++ source files found recursively")
        else:
            raise ConditionError(f"{input_path}: no such file or directory")
        for path in candidates:
            identity = path.resolve()
            if identity not in seen:
                seen.add(identity)
                paths.append(path)
    return paths


__all__ = ["SOURCE_SUFFIXES", "STDIN_PATH", "source_paths"]
