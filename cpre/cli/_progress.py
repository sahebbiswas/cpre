"""Progress for commands that work through many files, or one slow file.

For several inputs (``--recursive``, a directory) the display counts files. A command that
reports its progress inside a file (the macro analysis of ``cpre`` and ``cpre
simplify-macros``) also shows how far through the current file it is, so a single large
file is not silent either. A single file shows progress only once the run turns out to
be slow, and says nothing about a quick one.

Progress is written to standard error only, so standard output (reports, JSON, SARIF,
diffs) is byte-for-byte the same with progress on or off. There are three presentations:

- ``live``: one status line on a terminal, redrawn in place (about ten times a second);
- ``lines``: a plain line every ``interval`` seconds, for logs and ``TERM=dumb``;
- ``off``: nothing.

Anything else that prints while the status line is visible (a report, an error) does so
inside :func:`paused`, which clears the line first and redraws it afterwards.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import TextIO

from .._style import Style, ansi_capable, detect_style
from ._progress_text import (
    Snapshot,
    format_duration,
    interrupted_text,
    line_text,
    shorten_path,
    status_text,
    summary_text,
)

PROGRESS_CHOICES = ("auto", "always", "never")

_CLEAR_LINE = "\r\033[2K"
_REDRAW_INTERVAL = 0.1


class Progress:
    """Tracks one multi-file run and shows it on ``stream`` according to ``kind``."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        kind: str = "off",
        show_after: float = 0.5,
        interval: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        style: Style | None = None,
        columns: Callable[[], int] | None = None,
        threaded: bool = True,
        announce: bool = False,
        redraw: float = _REDRAW_INTERVAL,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._kind = kind
        self._show_after = show_after
        self._interval = interval
        self._redraw = redraw
        self._clock = clock
        self._style = style if style is not None else Style()
        self._columns = columns if columns is not None else self._terminal_columns
        self._threaded = threaded
        self._announce = announce  # print the closing summary even for a fast run
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self._start = clock()
        self._phase = "scanning"
        self._found = 0
        self._total = 0
        self._done = 0
        self._failed = 0
        self._findings = 0
        self._sizes: dict[Path, int] = {}
        self._total_bytes = 0
        self._done_bytes = 0
        self._analysis_start = self._start
        self._current: Path | None = None
        self._current_start = self._start
        self._macros_done = 0
        self._macros_total = 0
        self._macro: str | None = None
        self._macro_start = self._start
        self._last_draw = float("-inf")
        self._last_line = self._start
        self._drawn = False
        self._shown = False
        self._previous: Progress | None = None

    # -- construction ---------------------------------------------------------------

    @classmethod
    def create(
        cls,
        mode: str,
        *,
        batch: bool,
        within_file: bool = False,
        stream: TextIO | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        threaded: bool = True,
    ) -> Progress:
        """The reporter for ``--progress MODE``.

        Inert unless the run is a batch, or ``within_file`` says the command reports
        progress inside a file. Such a single-file run is shown only once it is slow (after
        half a second on a terminal, every ten seconds in a log), and it does not announce
        a closing summary when it is quick, even with ``always``.
        """
        target = stream if stream is not None else sys.stderr
        if mode == "never" or not (batch or within_file):
            return cls(target, kind="off", clock=clock, threaded=threaded)
        immediate = mode == "always" and batch
        environment = os.environ if env is None else env
        tty = bool(target.isatty())
        dumb = environment.get("TERM") == "dumb"
        style = detect_style(target, "auto", environment)
        if tty and not dumb and ansi_capable(target):
            return cls(
                target,
                kind="live",
                show_after=0.0 if immediate else 0.5,
                clock=clock,
                style=style,
                threaded=threaded,
                announce=immediate,
            )
        if mode == "always" or tty:
            # A log or a dumb terminal: plain lines, never cursor control.
            return cls(
                target,
                kind="lines",
                show_after=0.0 if immediate else 10.0,
                clock=clock,
                style=style,
                threaded=threaded,
                announce=immediate,
            )
        return cls(target, kind="off", clock=clock, threaded=threaded)

    @property
    def active(self) -> bool:
        return self._kind != "off"

    # -- life cycle -----------------------------------------------------------------

    def __enter__(self) -> Progress:
        global _active
        self._previous = _active
        _active = self
        self._start = self._last_line = self._clock()
        if self.active and self._threaded:
            self._ticker = threading.Thread(target=self._run_ticker, name="cpre-progress")
            self._ticker.daemon = True
            self._ticker.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        global _active
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join()
        with self._lock:
            self._clear()
            if self.active and exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
                self._write(f"cpre: {interrupted_text(self._snapshot(self._clock()))}\n")
            elif self.active and exc_type is None and (self._shown or self._announce):
                self._write(f"cpre: {summary_text(self._snapshot(self._clock()))}\n")
        _active = self._previous

    def _run_ticker(self) -> None:
        while not self._stop.wait(_REDRAW_INTERVAL):
            self.tick()

    # -- events ---------------------------------------------------------------------

    def scanning(self, found: int) -> None:
        """Discovery has found ``found`` source files so far."""
        with self._lock:
            self._found = found
        self.tick()

    def begin(self, paths: Sequence[Path]) -> None:
        """Discovery is over: ``paths`` are the files that will be analyzed, in order."""
        with self._lock:
            self._phase = "analyzing"
            self._total = len(paths)
            self._sizes = {}
            if self.active:
                for path in paths:
                    with contextlib.suppress(OSError):
                        self._sizes[path] = path.stat().st_size
            self._total_bytes = sum(self._sizes.values())
            self._analysis_start = self._clock()
        self.tick()

    def file_started(self, path: Path) -> None:
        with self._lock:
            self._current = path
            self._current_start = self._clock()
            self._reset_macros()
        self.tick()

    def macros(self, done: int, total: int, name: str | None) -> None:
        """The current file's macro analysis: ``done`` of ``total`` finished, ``name`` starting.

        Matches the ``on_progress`` callback of ``analyze_macros``.
        """
        with self._lock:
            self._macros_done = done
            self._macros_total = total
            if name != self._macro:
                self._macro_start = self._clock()
            self._macro = name
        self.tick()

    def _reset_macros(self) -> None:
        self._macros_done = self._macros_total = 0
        self._macro = None

    def file_done(self, findings: int = 0, *, failed: bool = False) -> None:
        with self._lock:
            self._done += 1
            self._findings += findings
            if failed:
                self._failed += 1
            if self._current is not None:
                self._done_bytes += self._sizes.get(self._current, 0)
            self._current = None
            self._reset_macros()
        self.tick()

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """Print on standard output or error inside this block without garbling the status line."""
        with self._lock:
            self._clear()
            try:
                yield
            finally:
                for stream in (sys.stdout, sys.stderr):
                    with contextlib.suppress(OSError, ValueError):
                        stream.flush()
                self.tick(force=True)

    # -- drawing --------------------------------------------------------------------

    def tick(self, *, force: bool = False) -> None:
        """Draw the status if it is due; called by events and by the redraw thread."""
        if not self.active:
            return
        with self._lock:
            now = self._clock()
            if now - self._start < self._show_after:
                return
            if self._kind == "live":
                if force or now - self._last_draw >= self._redraw:
                    self._draw(now)
            elif now - self._last_line >= self._interval:
                self._last_line = now
                self._write(f"cpre: {line_text(self._snapshot(now))}\n")
                self._shown = True

    def _draw(self, now: float) -> None:
        self._last_draw = now
        text = status_text(self._snapshot(now), self._style, self._columns())
        self._write(f"\r{text}\033[K")
        self._drawn = True
        self._shown = True

    def _clear(self) -> None:
        if self._drawn:
            self._write(_CLEAR_LINE)
            self._drawn = False

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except (OSError, ValueError):
            self._kind = "off"  # the stream is gone; stop trying

    def _terminal_columns(self) -> int:
        """The terminal width; a terminal that reports none (0 columns) falls back to 80."""
        try:
            columns = os.get_terminal_size(self._stream.fileno()).columns
        except (OSError, ValueError, AttributeError):
            columns = 0
        if columns > 0:
            return columns
        try:
            return int(os.environ.get("COLUMNS", "")) or 80
        except ValueError:
            return 80

    # -- text -----------------------------------------------------------------------

    def _snapshot(self, now: float) -> Snapshot:
        return Snapshot(
            phase=self._phase,
            found=self._found,
            total=self._total,
            done=self._done,
            failed=self._failed,
            findings=self._findings,
            elapsed=now - self._start,
            analysis_elapsed=now - self._analysis_start,
            current=self._current,
            running=now - self._current_start,
            done_bytes=self._done_bytes,
            total_bytes=self._total_bytes,
            macros_done=self._macros_done,
            macros_total=self._macros_total,
            macro=self._macro,
            macro_running=now - self._macro_start,
            current_bytes=self._sizes.get(self._current, 0) if self._current is not None else 0,
        )


_active: Progress | None = None


def paused() -> contextlib.AbstractContextManager[None]:
    """Guard for printing while a status line may be visible; a no-op when there is none."""
    reporter = _active
    if reporter is None or not reporter.active:
        return contextlib.nullcontext()
    return reporter.paused()


__all__ = ["PROGRESS_CHOICES", "Progress", "format_duration", "paused", "shorten_path"]
