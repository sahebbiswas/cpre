"""Terminal styling shared by every text report: color roles, glyphs and detection.

A :class:`Style` carries the two presentation switches a report needs, ANSI color and
Unicode glyphs, so a report builds the same text whether it is colored or not: color is
only ever added around text that is already readable without it.

Color follows one meaning per role (see ``docs/cli.md``):

- red marks what is dead or removed, yellow what is redundant or incomplete;
- green marks only the *result* of a transformation (the "after");
- cyan marks opaque predicates and headings; dim marks context and unchanged lines.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

COLOR_CHOICES = ("auto", "always", "never")

_ESC = "\033["
_RESET = "\033[0m"

# Role -> SGR parameters. The first group is the long-standing set of color names
# (``colored(text, "red", ...)``); "gray" is the dim attribute, not bright black, which
# is unreadable on several common terminal themes.
_SGR: dict[str, str] = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
    "gray": "2",
    "dim": "2",
    "bold": "1",
    "dead": "1;31",
    "redundant": "1;33",
    "incomplete": "1;33",
    "simplify": "1;32",
    "ok": "2",
    "skip": "2",
    "before": "31",
    "before_changed": "1;31",
    "after": "32",
    "after_changed": "1;32",
    "opaque": "36",
    "heading": "1;36",
}

_UNICODE_PROBE = "│→·▕█░…"


class _Stream(Protocol):
    @property
    def encoding(self) -> str | None: ...

    def isatty(self) -> bool: ...

    def fileno(self) -> int: ...


@dataclass(frozen=True)
class Style:
    """How one report is presented: with or without ANSI color, with Unicode or ASCII glyphs."""

    color: bool = False
    unicode: bool = False

    def paint(self, text: str, role: str) -> str:
        """``text`` in ``role``'s style, or unchanged when color is off or ``text`` is empty."""
        if not self.color or not text:
            return text
        return f"{_ESC}{_SGR[role]}m{text}{_RESET}"

    @property
    def guide(self) -> str:
        """One level of nesting in a conditional tree."""
        return "│ " if self.unicode else "| "

    @property
    def arrow(self) -> str:
        return "→" if self.unicode else "->"

    @property
    def separator(self) -> str:
        return " · " if self.unicode else ", "

    @property
    def ellipsis(self) -> str:
        return "…" if self.unicode else "..."

    def bar(self, fraction: float, width: int) -> str:
        """A progress bar ``width`` cells wide, filled to ``fraction`` (0 to 1)."""
        filled = max(0, min(width, int(fraction * width)))
        if self.unicode:
            return "▕" + "█" * filled + "░" * (width - filled) + "▏"
        return "[" + "#" * filled + "-" * (width - filled) + "]"


PLAIN = Style()


def ansi_capable(stream: _Stream) -> bool:
    """Whether ``stream`` is a terminal that interprets ANSI escape sequences.

    Everywhere but Windows a terminal does. A Windows console must have virtual-terminal
    processing switched on; it is enabled here when it can be, so modern consoles get color
    and a live status line while a legacy console falls back to plain output.
    """
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if not _is_windows():
        return True
    return _enable_windows_vt(stream)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _enable_windows_vt(stream: _Stream) -> bool:  # pragma: no cover - needs a Windows console
    try:
        import ctypes
        import msvcrt

        # Looked up by name: these exist only on Windows, so a static reference would not type check elsewhere.
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
        handle = getattr(msvcrt, "get_osfhandle")(stream.fileno())  # noqa: B009
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)):
            return False
        vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & vt:
            return True
        return bool(kernel32.SetConsoleMode(ctypes.c_void_p(handle), mode.value | vt))
    except (OSError, ValueError, AttributeError, ImportError):
        return False


def resolve_color(mode: str, stream: _Stream, env: Mapping[str, str] | None = None) -> bool:
    """Whether ANSI color should be used on ``stream`` under ``--color MODE``.

    ``always`` and ``never`` are absolute. ``auto`` follows the NO_COLOR and
    FORCE_COLOR conventions, then switches color off for ``TERM=dumb`` and for any
    stream that is not a terminal.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode != "auto":
        raise ValueError(f"unknown color mode: {mode!r}")
    environment = os.environ if env is None else env
    if environment.get("NO_COLOR"):
        return False
    if environment.get("FORCE_COLOR", "0") not in ("", "0"):
        return True
    if environment.get("TERM") == "dumb":
        return False
    return ansi_capable(stream)


def supports_unicode(stream: _Stream, env: Mapping[str, str] | None = None) -> bool:
    """Whether the glyphs of :class:`Style` can be written to ``stream`` as they are."""
    environment = os.environ if env is None else env
    if environment.get("TERM") == "dumb":
        return False
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        _UNICODE_PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def detect_style(
    stream: _Stream, mode: str = "auto", env: Mapping[str, str] | None = None
) -> Style:
    """The :class:`Style` for text written to ``stream`` under ``--color MODE``."""
    return Style(color=resolve_color(mode, stream, env), unicode=supports_unicode(stream, env))


def style_for(style: Style | None, color: bool) -> Style:
    """The style a report function uses: the explicit one, else plain text with ``color``."""
    return style if style is not None else Style(color=color)


_TOKEN = re.compile(r"\s+|\w+|&&|\|\||<<|>>|[<>=!]=|.")

# Below this similarity the two sides share too little for token highlighting to help; the
# whole line is then one color, which reads as "replaced".
_MIN_SIMILARITY = 0.34

Segments = list[tuple[str, bool]]


def _merge(segments: Segments) -> Segments:
    merged: Segments = []
    for text, changed in segments:
        if not text:
            continue
        if merged and merged[-1][1] == changed:
            merged[-1] = (merged[-1][0] + text, changed)
        else:
            merged.append((text, changed))
    return merged


def diff_segments(before: str, after: str) -> tuple[Segments, Segments]:
    """Split ``before`` and ``after`` into ``(text, changed)`` runs at token granularity.

    The runs of each side concatenate back to that side exactly. When the two share too
    little to be compared token by token, each is a single unchanged run.
    """
    old = _TOKEN.findall(before)
    new = _TOKEN.findall(after)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if matcher.ratio() < _MIN_SIMILARITY:
        return [(before, False)], [(after, False)]
    left: Segments = []
    right: Segments = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        same = tag == "equal"
        left.append(("".join(old[i1:i2]), not same))
        right.append(("".join(new[j1:j2]), not same))
    return _merge(left), _merge(right)


def paint_segments(style: Style, segments: Segments, plain_role: str, changed_role: str) -> str:
    """Join ``segments``, styling the changed runs more strongly than the rest."""
    return "".join(
        style.paint(text, changed_role if changed else plain_role) for text, changed in segments
    )


__all__ = [
    "COLOR_CHOICES",
    "PLAIN",
    "Segments",
    "Style",
    "ansi_capable",
    "detect_style",
    "diff_segments",
    "paint_segments",
    "resolve_color",
    "style_for",
    "supports_unicode",
]
