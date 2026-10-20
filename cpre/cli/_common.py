"""Pieces shared by every command: exit statuses, option groups and output helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._types import AnalysisOptions
from ..discovery import STDIN_PATH, source_paths
from ..errors import CpreError
from ..macro_analysis import _resolve_symbolic_literals
from ..model import ConditionError

# Exit statuses documented in docs/cli.md.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def add_symbolic_literal_options(parser: argparse.ArgumentParser, analysis: str) -> None:
    """The ``--symbolic-literal`` option and its ``--symbolic-zero`` / ``--symbolic-one`` shorthands.

    ``analysis`` names what the options affect.
    """
    parser.add_argument(
        "--symbolic-literal",
        dest="symbolic_literals",
        type=int,
        action="append",
        metavar="N",
        help=(
            f"treat integer literal N (0 or 1) as a symbolic Boolean atom in {analysis} "
            "analysis; may be repeated"
        ),
    )
    parser.add_argument(
        "--symbolic-zero",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 0",
    )
    parser.add_argument(
        "--symbolic-one",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 1",
    )


def symbolic_literals_from(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[int, ...]:
    """The validated literal list from the options above; a bad one is a usage error."""
    if not (args.symbolic_zero or args.symbolic_one or args.symbolic_literals):
        return ()
    raw_literals: list[int] = []
    if args.symbolic_zero:
        raw_literals.append(0)
    if args.symbolic_one:
        raw_literals.append(1)
    if args.symbolic_literals:
        raw_literals.extend(args.symbolic_literals)
    try:
        return _resolve_symbolic_literals(raw_literals)
    except CpreError as error:
        parser.error(error.message)


def add_limit_options(parser: argparse.ArgumentParser) -> None:
    """The ``--max-atoms`` / ``--max-bdd-nodes`` / ``--max-work`` resource limits."""
    parser.add_argument(
        "--max-atoms",
        type=int,
        metavar="N",
        help="resource limit: distinct Boolean atoms per independent component (default: 64)",
    )
    parser.add_argument(
        "--max-bdd-nodes",
        type=int,
        metavar="N",
        help="resource limit: decision-diagram nodes per independent component (default: 100000)",
    )
    parser.add_argument(
        "--max-work",
        type=int,
        metavar="N",
        help="resource limit: work units shared by the whole analysis (default: 500000)",
    )


def analysis_options_from(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> AnalysisOptions:
    """The ``AnalysisOptions`` selected by the limit options; an invalid value is a usage error."""
    limits = {
        key: value
        for key, value in (
            ("max_atoms", args.max_atoms),
            ("max_bdd_nodes", args.max_bdd_nodes),
            ("max_work", args.max_work),
        )
        if value is not None
    }
    try:
        return AnalysisOptions(**limits)
    except CpreError as error:
        parser.error(error.message)


def source_argument(value: str) -> Path:
    """The argparse type of a source operand: ``-`` is standard input, anything else a path.

    A file literally named ``-`` is reachable as ``./-``.
    """
    return STDIN_PATH if value == "-" else Path(value)


def is_stdin(path: Path) -> bool:
    """Whether ``path`` is the standard-input marker (by identity; see ``STDIN_PATH``)."""
    return path is STDIN_PATH


def read_source(path: Path, *, newline: str | None) -> str:
    """The UTF-8 text of a source file or of standard input.

    ``newline`` has the meaning it has for ``open()``: ``None`` translates line endings
    to ``\\n``, ``""`` keeps them exactly. Standard input is read in full, as bytes, so
    both behave the same for it as for a file. Undecodable input raises
    ``UnicodeDecodeError``, as for a file.
    """
    if is_stdin(path):
        buffer = getattr(sys.stdin, "buffer", None)
        text = buffer.read().decode("utf-8") if buffer is not None else sys.stdin.read()
        if newline is None:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text
    with open(path, encoding="utf-8", newline=newline) as handle:
        return handle.read()


def input_paths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[Path]:
    """The source files named by ``args.sources``, expanding directories when recursive."""
    if sum(1 for path in args.sources if is_stdin(path)) > 1:
        parser.error("standard input ('-') can be read only once")
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
