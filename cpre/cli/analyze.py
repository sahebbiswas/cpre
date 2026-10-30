"""The default command: analyze conditional directives and macros in source files."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__, reporting
from .._style import Style
from .._types import AnalysisOptions, AnalysisResult
from ..api import analyze_source
from ..errors import CpreError, ErrorCode
from ..macro_analysis import MacroAnalysisResult
from ..model import ConditionalTree
from ..sarif import ToolNotification, sarif_log
from ..serialization import batch_document, build_file_json, top_level
from ..text_reports import (
    file_findings,
    format_error,
    format_incomplete,
    format_incomplete_component,
    render_combined_report,
    summary_line,
    telemetry_lines,
)
from ._common import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_OK,
    add_expand_macros_option,
    add_limit_options,
    add_presentation_options,
    add_symbolic_literal_options,
    analysis_options_from,
    analyze_file_macros,
    input_paths,
    is_batch,
    output_style,
    progress_for,
    read_source,
    report_failure,
    source_argument,
    symbolic_literals_from,
)
from ._progress import Progress, paused


@dataclass
class _FileResult:
    path: Path
    tree: ConditionalTree | None
    macros: tuple[MacroAnalysisResult, ...]
    analysis: AnalysisResult | None


@dataclass
class _Run:
    """Everything the analysis loop collects before any result is written."""

    files: list[_FileResult] = field(default_factory=list)
    analyses: list[AnalysisResult] = field(default_factory=list)
    notifications: list[ToolNotification] = field(default_factory=list)
    had_errors: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze Boolean C/C++ preprocessor conditional directives and macros."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cpre {__version__}",
        help="show the installed version and exit",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=source_argument,
        help="C/C++ source files, directories used with --recursive, or - for standard input",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively scan C/C++ source files under directory inputs",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json", action="store_true", help="write the analysis results as JSON"
    )
    output_group.add_argument("--sarif", action="store_true", help="write findings as SARIF 2.1.0")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include unchanged conditional branches and macros in the report",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit with status 1 when a dead, redundant, or simplifiable branch/macro is found",
    )
    add_symbolic_literal_options(parser, "macro")
    add_expand_macros_option(parser)
    parser.add_argument(
        "--no-macros",
        action="store_true",
        help="disable macro Boolean simplification analysis",
    )
    parser.add_argument(
        "--no-conditionals",
        action="store_true",
        help="disable conditional directive analysis",
    )
    parser.add_argument(
        "--telemetry",
        action="store_true",
        help=(
            "diagnostic only: report how conditional analysis used its resources (atoms, "
            "components, nodes, work); a 'telemetry' object in --json output, otherwise "
            "lines on stderr; findings are unchanged"
        ),
    )
    add_limit_options(parser)
    add_presentation_options(parser)
    parser.add_argument(
        "--macros",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _analyze_conditionals(
    path: Path,
    source: str,
    run: _Run,
    *,
    options: AnalysisOptions,
    telemetry: bool,
    echo_telemetry: bool,
) -> tuple[ConditionalTree | None, AnalysisResult]:
    result = analyze_source(source, filename=str(path), options=options, telemetry=telemetry)
    run.analyses.append(result)
    if echo_telemetry and result.telemetry is not None:
        for line in telemetry_lines(result.telemetry):
            with paused():
                print(f"{path}: telemetry: {line}", file=sys.stderr)
    if not result.complete:
        for diagnostic in result.incomplete:
            if diagnostic.component is None:
                message = format_incomplete(diagnostic)
            else:
                component = result.incomplete_components[diagnostic.component]
                message = format_incomplete_component(component)
            report_failure(path, message)
        run.had_errors = True
    # A partial result still proves its findings for every other group.
    tree = result.tree if result.complete or result.partial else None
    return tree, result


def _record_failure(path: Path, error: Exception, run: _Run) -> None:
    """Report an unreadable or unanalyzable file on stderr and as a SARIF notification."""
    if isinstance(error, CpreError):
        report_failure(path, format_error(error))
        run.notifications.append(
            ToolNotification(
                message=error.message,
                descriptor_id=error.code,
                filename=str(path),
                location=error.location,
            )
        )
    else:
        report_failure(path, str(error))
        run.notifications.append(
            ToolNotification(
                message=str(error),
                descriptor_id=ErrorCode.SOURCE_READ_ERROR,
                filename=str(path),
            )
        )
    run.had_errors = True


def _analyze_files(
    paths: Sequence[Path],
    *,
    enable_conditionals: bool,
    enable_macros: bool,
    symbolic_literals: tuple[int, ...],
    options: AnalysisOptions,
    expand_macros: bool = False,
    telemetry: bool = False,
    echo_telemetry: bool = False,
    progress: Progress | None = None,
    on_file: Callable[[_FileResult], None] | None = None,
) -> _Run:
    """Analyze each path in order.

    ``on_file`` receives every successfully analyzed file as soon as it is done, which is
    how the text report is written while later files are still being analyzed.
    """
    run = _Run()
    for path in paths:
        if progress is not None:
            progress.file_started(path)
        had_errors, run.had_errors = run.had_errors, False
        findings = 0
        result: _FileResult | None = None
        try:
            source = read_source(path, newline=None)
            tree: ConditionalTree | None = None
            c_result: AnalysisResult | None = None
            if enable_conditionals:
                tree, c_result = _analyze_conditionals(
                    path,
                    source,
                    run,
                    options=options,
                    telemetry=telemetry,
                    echo_telemetry=echo_telemetry,
                )
            m_results: tuple[MacroAnalysisResult, ...] = ()
            if enable_macros:
                m_results, failed = analyze_file_macros(
                    path, source, symbolic_literals, options, expand_macros, progress
                )
                run.had_errors = run.had_errors or failed
            result = _FileResult(path, tree, m_results, c_result)
            run.files.append(result)
            findings = file_findings(tree, m_results).total
        except (CpreError, OSError, UnicodeDecodeError) as error:
            _record_failure(path, error, run)
        failed = run.had_errors
        if result is not None and on_file is not None:
            on_file(result)
        run.had_errors = had_errors or failed
        if progress is not None:
            progress.file_done(findings, failed=failed)
    return run


def _file_json(
    file: _FileResult, args: argparse.Namespace, enable_macros: bool, literals: tuple[int, ...]
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


def _combined_report(file: _FileResult, *, verbose: bool, style: Style) -> tuple[str, bool]:
    return render_combined_report(
        file.tree,
        file.macros,
        verbose=verbose,
        style=style,
        incomplete_components=file.analysis.incomplete_components if file.analysis else (),
    )


class _TextStreamer:
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

    def __call__(self, file: _FileResult) -> None:
        self.counts += file_findings(file.tree, file.macros)
        report, has_entries = _combined_report(file, verbose=self._verbose, style=self._style)
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


def _write_results(
    run: _Run,
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
                f_data, has_visible = _file_json(file, args, enable_macros, symbolic_literals)
                if args.verbose or has_visible:
                    documents.append(f_data)
            print(json.dumps(batch_document("analysis", documents), indent=2))
        elif files:
            f_data, _ = _file_json(files[0], args, enable_macros, symbolic_literals)
            print(json.dumps(top_level("analysis", f_data), indent=2))


def _has_findings(files: Sequence[_FileResult]) -> bool:
    for file in files:
        if file.tree is not None and reporting.has_findings(file.tree):
            return True
        if any(r.simplified for r in file.macros):
            return True
    return False


def analyze_main(argv: Sequence[str] | None = None) -> int:
    """Analyze the named sources and write the report, JSON or SARIF to stdout."""
    parser = build_parser()
    args = parser.parse_args(argv)
    symbolic_literals = symbolic_literals_from(parser, args)
    options = analysis_options_from(parser, args)
    enable_macros = not args.no_macros
    if args.expand_macros and not enable_macros:
        parser.error("--expand-macros has no effect with --no-macros")
    if args.expand_macros and args.sarif:
        parser.error("--expand-macros has no effect with --sarif: SARIF has no macro findings")
    if args.telemetry and args.no_conditionals:
        parser.error(
            "--telemetry describes conditional analysis; it cannot be used with --no-conditionals"
        )
    if args.telemetry and args.sarif:
        parser.error("--telemetry cannot be combined with --sarif")
    batch = is_batch(args)
    text = not (args.json or args.sarif)
    streamer = _TextStreamer(args, output_style(args), batch=batch) if text else None
    with progress_for(args, batch=batch) as progress:
        paths = input_paths(parser, args, progress)
        progress.begin(paths)
        run = _analyze_files(
            paths,
            enable_conditionals=not args.no_conditionals,
            enable_macros=enable_macros,
            symbolic_literals=symbolic_literals,
            options=options,
            expand_macros=args.expand_macros,
            telemetry=args.telemetry,
            echo_telemetry=args.telemetry and not args.json,
            progress=progress,
            on_file=streamer,
        )
    if streamer is not None:
        streamer.finish()
    else:
        _write_results(
            run,
            args,
            batch=batch,
            enable_macros=enable_macros,
            symbolic_literals=symbolic_literals,
        )
    if run.had_errors:
        return EXIT_ERROR
    if args.fail_on_findings and _has_findings(run.files):
        return EXIT_FINDINGS
    return EXIT_OK
