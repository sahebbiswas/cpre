"""Internal C/C++ source path discovery used by the CLI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .model import ConditionError

SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})


STDIN_PATH = Path("<stdin>")
"""Standard input as a source: the logical filename every message, JSON field and
``__FILE__`` uses for it. The command line spells it ``-``."""


def source_paths(inputs: Sequence[Path], recursive: bool) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for input_path in inputs:
        if input_path == STDIN_PATH:
            if STDIN_PATH not in seen:
                seen.add(STDIN_PATH)
                paths.append(STDIN_PATH)
            continue
        if input_path.is_file():
            candidates = [input_path]
        elif input_path.is_dir():
            if not recursive:
                raise ConditionError(f"{input_path}: is a directory; use --recursive to scan it")
            candidates = sorted(
                path
                for path in input_path.rglob("*")
                if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
            )
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
