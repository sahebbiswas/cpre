"""The physical line model shared by every cpre component.

A physical line ends at ``\\r\\n``, a lone ``\\r`` or ``\\n``, the end-of-line
markers GCC and Clang accept. Form feed, vertical tab and the other separators
``str.splitlines`` honours are ordinary characters within a line.
"""

from __future__ import annotations

import re

LINE_BREAK = re.compile(r"\r\n|\r|\n")
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def split_lines(text: str, keepends: bool = False) -> list[str]:
    """Split ``text`` into physical lines, like ``str.splitlines`` but only at line breaks."""
    lines = _LINE.findall(text)
    return lines if keepends else [line.rstrip("\r\n") for line in lines]


def line_starts(text: str) -> list[int]:
    """Offset where each physical line starts, including a final empty line after a break."""
    return [0, *(match.end() for match in LINE_BREAK.finditer(text))]
