"""How the default command presents analyzed files: text streaming, JSON and SARIF."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__, reporting
from .._style import Style
from .._types import AnalysisResult
from ..macro_analysis import MacroAnalysisResult
from ..model import ConditionalTree
from ..sarif import ToolNotification, sarif_log
from ..serialization import batch_document, build_file_json, top_level
from ..text_reports import file_findings, render_combined_report, summary_line
from ._progress import paused


@dataclass
class Run:
    """Everything the analysis loop collects before any result is written."""

    files: list[FileResult] = field(default_factory=list)
    analyses: list[AnalysisResult] = field(default_factory=list)
    notifications: list[ToolNotification] = field(default_factory=list)
    had_errors: bool = False


@dataclass
class FileResult:
    path: Path
    tree: ConditionalTree | None
    macros: tuple[MacroAnalysisResult, ...]
    analysis: AnalysisResult | None


def file_json(
    file: FileResult, args: argparse.Namespace, enable_macros: bool, literals: tuple[int, ...]
) -> tuple[dict[str, object], bool]:
    return build_file_json(
        file.path,
        file.tree,
        file.macros,
        verbose=args.verbose,
        enable_macros=enable_macros,
        symbolic_literals=literals,
        analysis=file.analysis,
        expand_macros=args.expand_macros,
    )


def combined_report(file: FileResult, *, verbose: bool, style: Style) -> tuple[str, bool]:
    return render_combined_report(
        file.tree,
        file.macros,
        verbose=verbose,
        style=style,
        incomplete_components=file.analysis.incomplete_components if file.analysis else (),
    )


class TextStreamer:
    """Writes each file's text report as the file finishes, then the closing summary.

    The first report needs no separator; every later one is preceded by a blank line, so
    the output equals the reports joined by blank lines, whenever they are written.
    """

    def __init__(self, args: argparse.Namespace, style: Style, *, batch: bool) -> None:
        self._verbose = args.verbose
        self._style = style
        self._batch = batch
        self._written = False
        self.counts = reporting.FindingCounts()

    def __call__(self, file: FileResult) -> None:
        self.counts += file_findings(file.tree, file.macros)
        report, has_entries = combined_report(file, verbose=self._verbose, style=self._style)
        if self._batch and not (self._verbose or has_entries):
            return
        heading = f"{self._style.paint(f'== {file.path} ==', 'heading')}\n" if self._batch else ""
        with paused():
            if self._written and self._batch:
                print()
            print(f"{heading}{report}")
        self._written = True

    def finish(self) -> None:
        summary = summary_line(self.counts, self._style)
        if summary:
            with paused():
                print()
                print(summary)


def write_results(
    run: Run,
    args: argparse.Namespace,
    *,
    batch: bool,
    enable_macros: bool,
    symbolic_literals: tuple[int, ...],
) -> None:
    """Write everything that is not streamed: SARIF and JSON documents."""
    files = run.files
    if args.sarif:
        log = sarif_log(run.analyses, tool_version=__version__, notifications=run.notifications)
        print(json.dumps(log, indent=2))
    elif args.json:
        if batch:
            documents = []
            for file in files:
                f_data, has_visible = file_json(file, args, enable_macros, symbolic_literals)
                if args.verbose or has_visible:
                    documents.append(f_data)
            print(json.dumps(batch_document("analysis", documents), indent=2))
        elif files:
            f_data, _ = file_json(files[0], args, enable_macros, symbolic_literals)
            print(json.dumps(top_level("analysis", f_data), indent=2))


def has_findings(files: Sequence[FileResult]) -> bool:
    for file in files:
        if file.tree is not None and reporting.has_findings(file.tree):
            return True
        if any(r.simplified for r in file.macros):
            return True
    return False
