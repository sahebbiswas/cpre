"""The default command: analyze conditional directives and macros in source files."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__, reporting
from .._types import AnalysisResult
from ..api import analyze_source
from ..errors import CpreError, ErrorCode
from ..macro_analysis import MacroAnalysisResult, analyze_macros
from ..model import ConditionalTree
from ..sarif import ToolNotification, sarif_log
from ..serialization import batch_document, build_file_json, top_level
from ..text_reports import (
    format_error,
    format_incomplete,
    format_incomplete_component,
    render_combined_report,
)
from ._common import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_OK,
    add_symbolic_literal_options,
    input_paths,
    is_batch,
    report_failure,
    symbolic_literals_from,
)


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
        type=Path,
        help="C/C++ source files, or directories used with --recursive",
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
        "--macros",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _analyze_conditionals(
    path: Path, source: str, run: _Run
) -> tuple[ConditionalTree | None, AnalysisResult]:
    result = analyze_source(source, filename=str(path))
    run.analyses.append(result)
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


def _analyze_macros(
    path: Path, source: str, symbolic_literals: tuple[int, ...], run: _Run
) -> tuple[MacroAnalysisResult, ...]:
    results = analyze_macros(source, filename=str(path), symbolic_literals=symbolic_literals)
    for r in results:
        if not r.complete and r.incomplete:
            report_failure(path, format_incomplete(r.incomplete))
            run.had_errors = True
    return results


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
) -> _Run:
    run = _Run()
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            tree: ConditionalTree | None = None
            c_result: AnalysisResult | None = None
            if enable_conditionals:
                tree, c_result = _analyze_conditionals(path, source, run)
            m_results: tuple[MacroAnalysisResult, ...] = ()
            if enable_macros:
                m_results = _analyze_macros(path, source, symbolic_literals, run)
            run.files.append(_FileResult(path, tree, m_results, c_result))
        except CpreError as error:
            _record_failure(path, error, run)
        except (OSError, UnicodeDecodeError) as error:
            _record_failure(path, error, run)
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
    )


def _combined_report(file: _FileResult, *, verbose: bool, color: bool) -> tuple[str, bool]:
    return render_combined_report(
        file.tree,
        file.macros,
        verbose=verbose,
        color=color,
        incomplete_components=file.analysis.incomplete_components if file.analysis else (),
    )


def _write_results(
    run: _Run,
    args: argparse.Namespace,
    *,
    batch: bool,
    enable_macros: bool,
    symbolic_literals: tuple[int, ...],
) -> None:
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
    elif batch:
        color = sys.stdout.isatty()
        reports = []
        for file in files:
            report, has_entries = _combined_report(file, verbose=args.verbose, color=color)
            if args.verbose or has_entries:
                heading = reporting.colored(f"== {file.path} ==", "cyan", color)
                reports.append("\n".join((heading, report)))
        if reports:
            print("\n\n".join(reports))
    elif files:
        report, _ = _combined_report(files[0], verbose=args.verbose, color=sys.stdout.isatty())
        print(report)


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
    paths = input_paths(parser, args)
    enable_macros = not args.no_macros
    run = _analyze_files(
        paths,
        enable_conditionals=not args.no_conditionals,
        enable_macros=enable_macros,
        symbolic_literals=symbolic_literals,
    )
    _write_results(
        run,
        args,
        batch=is_batch(args),
        enable_macros=enable_macros,
        symbolic_literals=symbolic_literals,
    )
    if run.had_errors:
        return EXIT_ERROR
    if args.fail_on_findings and _has_findings(run.files):
        return EXIT_FINDINGS
    return EXIT_OK
