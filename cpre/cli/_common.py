"""Pieces shared by every command: exit statuses, option groups and output helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..discovery import source_paths
from ..errors import CpreError
from ..macro_analysis import _resolve_symbolic_literals
from ..model import ConditionError

# Exit statuses documented in docs/cli.md.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def add_symbolic_literal_options(parser: argparse.ArgumentParser, analysis: str) -> None:
    """The ``--symbolic-literal`` / ``--symbolic-zero`` pair; ``analysis`` names what it affects."""
    parser.add_argument(
        "--symbolic-literal",
        dest="symbolic_literals",
        type=int,
        action="append",
        metavar="N",
        help=f"treat integer literal N as a symbolic Boolean atom in {analysis} analysis (e.g. 0)",
    )
    parser.add_argument(
        "--symbolic-zero",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 0",
    )


def symbolic_literals_from(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[int, ...]:
    """The validated literal list from the options above; a bad one is a usage error."""
    if not (args.symbolic_zero or args.symbolic_literals):
        return ()
    raw_literals: list[int] = []
    if args.symbolic_zero:
        raw_literals.append(0)
    if args.symbolic_literals:
        raw_literals.extend(args.symbolic_literals)
    try:
        return _resolve_symbolic_literals(raw_literals)
    except CpreError as error:
        parser.error(error.message)


def input_paths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[Path]:
    """The source files named by ``args.sources``, expanding directories when recursive."""
    try:
        return source_paths(args.sources, args.recursive)
    except ConditionError as error:
        parser.error(str(error))


def is_batch(args: argparse.Namespace) -> bool:
    """Several inputs, or a directory, switch the output to its batch presentation."""
    return len(args.sources) > 1 or any(path.is_dir() for path in args.sources)


def write_exact(text: str) -> None:
    """Write to stdout without newline translation, so CRLF diffs stay applicable."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def report_failure(path: Path | str, message: str) -> None:
    """The ``PATH: message`` line every command prints on stderr for a failed input."""
    print(f"{path}: {message}", file=sys.stderr)
