"""``--consume-pragma``: the command-line pragma policy for the concrete commands."""

from __future__ import annotations

import argparse

from ..lexer import tokenize
from ..pragmas import Pragma, PragmaDisposition, PragmaHandler


def _words(payload: str) -> tuple[str, ...]:
    """The preprocessing tokens of a pragma payload, without whitespace and comments."""
    return tuple(t.text for t in tokenize(payload) if t.kind not in {"space", "comment"})


def add_pragma_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--consume-pragma",
        dest="consume_pragmas",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "accept reachable pragmas whose payload starts with the tokens of PREFIX and remove "
            "them from the output, e.g. 'once', 'pack', 'GCC diagnostic'; every other reachable "
            "pragma still makes the result incomplete; repeatable"
        ),
    )


def pragma_handler_from(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> PragmaHandler | None:
    """The handler for ``--consume-pragma``, or ``None`` when no prefix was given.

    A prefix matches a ``#pragma`` directive or ``_Pragma`` operator when its tokens are the
    first tokens of the payload, so ``pack`` accepts ``pack(push, 1)`` but not ``packed``.
    """
    prefixes: list[tuple[str, ...]] = []
    for value in args.consume_pragmas:
        words = _words(value)
        if not words or "\n" in value or "\r" in value:
            parser.error(f"--consume-pragma: {value!r} is not a pragma payload prefix")
        prefixes.append(words)
    if not prefixes:
        return None

    def handler(pragma: Pragma) -> PragmaDisposition | None:
        words = _words(pragma.payload)
        if any(words[: len(prefix)] == prefix for prefix in prefixes):
            return PragmaDisposition.CONSUME
        return PragmaDisposition.UNSUPPORTED

    return handler
