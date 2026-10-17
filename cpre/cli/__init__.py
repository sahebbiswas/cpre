"""Command-line interface: ``cpre`` and ``python -m cpre``.

``main`` only dispatches. Each command lives in its own module; the JSON and text it
prints are built by :mod:`cpre.serialization` and :mod:`cpre.text_reports`, so other
front ends can reuse them without importing any command.

- :mod:`.analyze`: the default command, conditional and macro analysis;
- :mod:`.simplify_macros`: ``simplify-macros`` (alias ``analyze-macros``);
- :mod:`.preprocess` and :mod:`.preprocess_configuration`: ``preprocess``;
- :mod:`.boolean_expression`: ``test-input``;
- :mod:`._common`: exit statuses and the options and helpers the commands share.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from ..text_reports import unified_diff as _unified_diff
from .analyze import analyze_main
from .boolean_expression import test_input_main
from .preprocess import preprocess_main
from .simplify_macros import simplify_macros_main

__all__ = [
    "analyze_main",
    "main",
    "preprocess_main",
    "simplify_macros_main",
    "test_input_main",
]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the cpre command-line interface."""
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in ("simplify-macros", "analyze-macros"):
        return simplify_macros_main(argv[1:], prog=f"cpre {argv[0]}")
    if argv and argv[0] == "preprocess":
        return preprocess_main(argv[1:], prog="cpre preprocess")
    if argv and argv[0] == "test-input":
        return test_input_main(argv[1:], prog="cpre test-input")
    return analyze_main(argv)
