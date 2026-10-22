"""The text of the progress display, as pure functions of a :class:`Snapshot`.

Nothing here reads a clock or writes to a stream, so the wording, the fitting to a
terminal width and the time estimate are all testable on their own;
:class:`cpre.cli._progress.Progress` supplies the snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .._style import Style

BAR_WIDTH = 10
MIN_PATH_WIDTH = 12
SLOW_FILE_SECONDS = 2.0
ETA_MIN_SECONDS = 3.0
ETA_MIN_FRACTION = 0.05


@dataclass(frozen=True)
class Snapshot:
    """Where a run stands at one moment."""

    phase: str  # "scanning" while discovering files, then "analyzing"
    found: int  # source files discovered so far
    total: int  # files to analyze (known once scanning is over)
    done: int
    failed: int
    findings: int
    elapsed: float  # seconds since the run started
    analysis_elapsed: float  # seconds since analysis began
    current: Path | None
    running: float  # seconds the current file has been running
    done_bytes: int
    total_bytes: int


def format_duration(seconds: float, *, precise: bool = False) -> str:
    """``9s``, ``2m05s`` or ``1h02m``; with ``precise``, seconds keep one decimal below a minute."""
    if seconds < 60:
        return f"{seconds:.1f}s" if precise else f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def shorten_path(text: str, width: int, ellipsis: str = "…") -> str:
    """``text`` cut from the left to ``width`` characters, keeping the file name end."""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[-width:] if width > 0 else ""
    return ellipsis + text[-(width - len(ellipsis)) :]


def plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def strip_ansi(text: str) -> str:
    """``text`` without ANSI styling, for measuring its visible width."""
    out = []
    skipping = False
    for char in text:
        if char == "\033":
            skipping = True
        elif skipping:
            skipping = char != "m"
        else:
            out.append(char)
    return "".join(out)


def eta_seconds(snapshot: Snapshot) -> float | None:
    """Estimated seconds left, weighted by file size; ``None`` until it would mean something."""
    if snapshot.total == 0 or snapshot.done == 0 or snapshot.done >= snapshot.total:
        return None
    if snapshot.total_bytes and snapshot.done_bytes:
        fraction = snapshot.done_bytes / snapshot.total_bytes
    else:
        fraction = snapshot.done / snapshot.total
    if snapshot.analysis_elapsed < ETA_MIN_SECONDS or fraction < ETA_MIN_FRACTION:
        return None
    return snapshot.analysis_elapsed * (1 - fraction) / fraction


def status_text(snapshot: Snapshot, style: Style, columns: int) -> str:
    """The live status line, fitted to ``columns``."""
    width = max(20, columns - 1)
    if snapshot.phase == "scanning":
        text = (
            f"scanning{style.ellipsis} {plural(snapshot.found, 'file')}"
            f"  {format_duration(snapshot.elapsed)}"
        )
        return text[:width]
    fraction = snapshot.done / snapshot.total if snapshot.total else 1.0
    pieces = [f"{snapshot.done}/{snapshot.total}"]
    if width >= 50:
        pieces.append(style.paint(style.bar(fraction, BAR_WIDTH), "cyan"))
    pieces.append(f"{int(fraction * 100):>3}%")
    eta = eta_seconds(snapshot)
    if eta is not None:
        pieces.append(f"~{format_duration(eta)}")
    if snapshot.findings:
        pieces.append(f"{snapshot.findings:,} found")
    if snapshot.failed:
        pieces.append(f"{snapshot.failed} failed")
    while len(pieces) > 2 and len(strip_ansi("  ".join(pieces))) > width:
        pieces.pop()  # a line wider than the terminal would wrap and break the redraw
    left = "  ".join(pieces)
    remaining = width - len(strip_ansi(left)) - 2
    if snapshot.current is None or remaining < MIN_PATH_WIDTH:
        return left
    suffix = ""
    if snapshot.running >= SLOW_FILE_SECONDS:
        candidate = f" {format_duration(snapshot.running)}"
        if remaining - len(candidate) >= MIN_PATH_WIDTH:
            suffix = candidate  # reserved first, so a long path never crowds it out
    name = shorten_path(str(snapshot.current), remaining - len(suffix), style.ellipsis)
    return f"{left}  {style.paint(name, 'dim')}{suffix}"


def line_text(snapshot: Snapshot) -> str:
    """The body of one heartbeat line."""
    elapsed = format_duration(snapshot.elapsed)
    if snapshot.phase == "scanning":
        return f"scanning, {plural(snapshot.found, 'file')} found, {elapsed}"
    percent = int(100 * snapshot.done / snapshot.total) if snapshot.total else 100
    parts = [f"{snapshot.done}/{snapshot.total} files ({percent}%)"]
    if snapshot.findings:
        parts.append(plural(snapshot.findings, "finding"))
    if snapshot.failed:
        parts.append(f"{snapshot.failed} failed")
    parts.append(f"{elapsed} elapsed")
    return ", ".join(parts)


def summary_text(snapshot: Snapshot) -> str:
    parts = [
        f"{plural(snapshot.done, 'file')} in {format_duration(snapshot.elapsed, precise=True)}"
    ]
    if snapshot.findings:
        parts.append(plural(snapshot.findings, "finding"))
    if snapshot.failed:
        parts.append(f"{snapshot.failed} failed")
    return ", ".join(parts)


def interrupted_text(snapshot: Snapshot) -> str:
    if snapshot.phase == "scanning":
        return f"interrupted while scanning ({plural(snapshot.found, 'file')} found)"
    return f"interrupted after {snapshot.done}/{snapshot.total} files"
