"""``cpre simplify-macros``: find, preview and apply proven macro simplifications."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .. import reporting
from .._style import Style
from .._types import AnalysisOptions
from ..errors import CpreError
from ..macro_analysis import MacroSimplificationResult, simplify_macros
from ..serialization import (
    batch_document,
    build_simplify_macros_json,
    build_simplify_macros_preview_json,
    top_level,
)
from ..text_reports import (
    count_macro_findings,
    format_error,
    format_incomplete,
    render_macro_report,
    summary_line,
    unified_diff,
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
    input_paths,
    is_batch,
    is_stdin,
    output_style,
    progress_for,
    read_source,
    report_failure,
    source_argument,
    symbolic_literals_from,
    write_exact,
)
from ._progress import Progress, paused

_FileResults = list[tuple[Path, MacroSimplificationResult]]


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Analyze and optionally rewrite simplified object-like macro definitions.",
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
    parser.add_argument(
        "--rewrite",
        "--in-place",
        dest="rewrite",
        action="store_true",
        help="rewrite source files in-place with proven-equivalent simplified definitions",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="print the rewrite as a unified diff instead of a report; never modifies files",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "exit with status 1 if --rewrite would change any file, naming each such file "
            "on stderr; never modifies files"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the analysis results as JSON (with --diff/--check, the preview result)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include unchanged and skipped macros in the report",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit with status 1 when simplifiable macros are found",
    )
    add_symbolic_literal_options(parser, "macro")
    add_expand_macros_option(parser)
    add_limit_options(parser)
    add_presentation_options(parser)
    return parser


def _simplify_files(
    paths: Sequence[Path],
    args: argparse.Namespace,
    symbolic_literals: tuple[int, ...],
    options: AnalysisOptions,
    progress: Progress | None = None,
    on_file: Callable[[Path, MacroSimplificationResult], None] | None = None,
) -> tuple[_FileResults, bool]:
    """Simplify (and with ``--rewrite`` write) each file; also whether any failed.

    ``on_file`` receives every successfully processed file as soon as it is done.
    """
    preview = args.diff or args.check
    file_results: _FileResults = []
    had_errors = False
    for path in paths:
        if progress is not None:
            progress.file_started(path)
        failed = False
        findings = 0
        finished: MacroSimplificationResult | None = None  # set only once the file is fully handled
        try:
            source = read_source(path, newline="")
            res = simplify_macros(
                source,
                filename=str(path),
                # --diff and --check run the same rewrite and verification as
                # --rewrite, so they report exactly what it would write.
                rewrite=args.rewrite or preview,
                symbolic_literals=symbolic_literals,
                options=options,
                expand_macros=args.expand_macros,
                on_progress=progress.macros if progress is not None else None,
            )
            for r in res.results:
                if not r.complete and r.incomplete:
                    report_failure(path, format_incomplete(r.incomplete))
                    failed = True
            if args.rewrite and res.rewritten:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(res.rewritten_source)
            file_results.append((path, res))
            finished = res
            findings = count_macro_findings(res.results).total
        except CpreError as error:
            report_failure(path, format_error(error))
            failed = True
        except (OSError, UnicodeDecodeError) as error:
            report_failure(path, str(error))
            failed = True
        had_errors = had_errors or failed
        if finished is not None and on_file is not None:
            on_file(path, finished)
        if progress is not None:
            progress.file_done(findings, failed=failed)
    return file_results, had_errors


def _write_json(
    file_results: _FileResults,
    args: argparse.Namespace,
    *,
    batch: bool,
    symbolic_literals: tuple[int, ...],
) -> None:
    if batch:
        files = []
        for path, res in file_results:
            f_data, has_visible = build_simplify_macros_json(
                path,
                res,
                verbose=args.verbose,
                symbolic_literals=symbolic_literals,
                expand_macros=args.expand_macros,
            )
            if args.verbose or has_visible:
                files.append(f_data)
        print(json.dumps(batch_document("simplify-macros", files), indent=2))
    elif file_results:
        f_data, _ = build_simplify_macros_json(
            file_results[0][0],
            file_results[0][1],
            verbose=args.verbose,
            symbolic_literals=symbolic_literals,
            expand_macros=args.expand_macros,
        )
        print(json.dumps(top_level("simplify-macros", f_data), indent=2))


def _write_preview_json(
    file_results: _FileResults,
    args: argparse.Namespace,
    *,
    batch: bool,
    symbolic_literals: tuple[int, ...],
) -> None:
    """The ``--check``/``--diff`` ``--json`` document; nothing here was written."""
    records = []
    for path, res in file_results:
        diff = unified_diff(path, res.source, res.rewritten_source) if args.diff else None
        record, would_change = build_simplify_macros_preview_json(
            path, res, diff=diff, symbolic_literals=symbolic_literals
        )
        if would_change or args.verbose or not batch:
            records.append(record)
    if batch:
        print(json.dumps(batch_document("simplify-macros-preview", records), indent=2))
    elif records:
        print(json.dumps(top_level("simplify-macros-preview", records[0]), indent=2))


class _ResultStreamer:
    """Writes each file's report (or, with ``--diff``, its diff) as the file finishes."""

    def __init__(self, args: argparse.Namespace, style: Style, *, batch: bool) -> None:
        self._args = args
        self._style = style
        self._batch = batch
        self._written = False
        self.simplifiable = 0

    def __call__(self, path: Path, res: MacroSimplificationResult) -> None:
        if self._args.diff:
            if res.rewritten_source != res.source:
                with paused():
                    write_exact(unified_diff(path, res.source, res.rewritten_source))
            return
        if self._args.check:
            return
        self.simplifiable += count_macro_findings(res.results).macros
        report, has_entries = render_macro_report(
            res.results,
            verbose=self._args.verbose,
            style=self._style,
            rewritten=self._args.rewrite,
        )
        if self._batch and not (self._args.verbose or has_entries):
            return
        heading = f"{self._style.paint(f'== {path} ==', 'heading')}\n" if self._batch else ""
        with paused():
            if self._written and self._batch:
                print()
            print(f"{heading}{report}")
        self._written = True

    def finish(self) -> None:
        """The closing summary; a rewrite reports what it did, so it adds none."""
        if self._args.rewrite or self._args.diff or self._args.check:
            return
        summary = summary_line(reporting.FindingCounts(macros=self.simplifiable), self._style)
        if summary:
            with paused():
                print()
                print(summary)


def simplify_macros_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre simplify-macros",
) -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    preview = args.diff or args.check
    if args.rewrite and preview:
        parser.error("--rewrite cannot be combined with --diff or --check")
    if args.expand_macros and (args.rewrite or preview):
        parser.error(
            "--expand-macros cannot be combined with --rewrite, --in-place, --diff or --check: "
            "a simplification found by expanding other macros is reported but never applied"
        )
    if args.rewrite and any(is_stdin(path) for path in args.sources):
        parser.error(
            "standard input cannot be rewritten in place; use --diff or --check, or name a file"
        )
    symbolic_literals = symbolic_literals_from(parser, args)
    options = analysis_options_from(parser, args)
    batch = is_batch(args)
    streamer = None if args.json else _ResultStreamer(args, output_style(args), batch=batch)

    with progress_for(args, batch=batch) as progress:
        paths = input_paths(parser, args, progress)
        progress.begin(paths)
        file_results, had_errors = _simplify_files(
            paths, args, symbolic_literals, options, progress=progress, on_file=streamer
        )

    changed = [(path, res) for path, res in file_results if res.rewritten_source != res.source]
    if streamer is not None:
        streamer.finish()
    if preview:
        if args.json:
            _write_preview_json(
                file_results, args, batch=batch, symbolic_literals=symbolic_literals
            )
        if args.check:
            for path, _ in changed:
                print(f"would rewrite {path}", file=sys.stderr)
    elif args.json:
        _write_json(file_results, args, batch=batch, symbolic_literals=symbolic_literals)

    if had_errors:
        return EXIT_ERROR
    if args.check and changed:
        return EXIT_FINDINGS
    has_findings = any(res.has_findings for _, res in file_results)
    return EXIT_FINDINGS if args.fail_on_findings and has_findings else EXIT_OK
