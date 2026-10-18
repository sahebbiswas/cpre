"""``cpre simplify-macros``: find, preview and apply proven macro simplifications."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import reporting
from ..errors import CpreError
from ..macro_analysis import MacroSimplificationResult, simplify_macros
from ..serialization import batch_document, build_simplify_macros_json, top_level
from ..text_reports import (
    format_error,
    format_incomplete,
    render_macro_report,
    unified_diff,
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
    write_exact,
)

_FileResults = list[tuple[Path, MacroSimplificationResult]]


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Analyze and optionally rewrite simplified object-like macro definitions.",
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
        help="write the analysis results as JSON",
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
    return parser


def _simplify_files(
    paths: Sequence[Path], args: argparse.Namespace, symbolic_literals: tuple[int, ...]
) -> tuple[_FileResults, bool]:
    """Simplify (and with ``--rewrite`` write) each file; also whether any failed."""
    preview = args.diff or args.check
    file_results: _FileResults = []
    had_errors = False
    for path in paths:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                source = f.read()
            res = simplify_macros(
                source,
                filename=str(path),
                # --diff and --check run the same rewrite and verification as
                # --rewrite, so they report exactly what it would write.
                rewrite=args.rewrite or preview,
                symbolic_literals=symbolic_literals,
            )
            for r in res.results:
                if not r.complete and r.incomplete:
                    report_failure(path, format_incomplete(r.incomplete))
                    had_errors = True
            if args.rewrite and res.rewritten:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(res.rewritten_source)
            file_results.append((path, res))
        except CpreError as error:
            report_failure(path, format_error(error))
            had_errors = True
        except (OSError, UnicodeDecodeError) as error:
            report_failure(path, str(error))
            had_errors = True
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
                path, res, verbose=args.verbose, symbolic_literals=symbolic_literals
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
        )
        print(json.dumps(top_level("simplify-macros", f_data), indent=2))


def _write_reports(file_results: _FileResults, args: argparse.Namespace, *, batch: bool) -> None:
    if batch:
        color = sys.stdout.isatty()
        reports = []
        for path, res in file_results:
            report, has_entries = render_macro_report(
                res.results,
                verbose=args.verbose,
                color=color,
                rewritten=args.rewrite,
            )
            if args.verbose or has_entries:
                heading = reporting.colored(f"== {path} ==", "cyan", color)
                reports.append("\n".join((heading, report)))
        if reports:
            print("\n\n".join(reports))
    elif file_results:
        report, _ = render_macro_report(
            file_results[0][1].results,
            verbose=args.verbose,
            color=sys.stdout.isatty(),
            rewritten=args.rewrite,
        )
        print(report)


def simplify_macros_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre simplify-macros",
) -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    preview = args.diff or args.check
    if args.rewrite and preview:
        parser.error("--rewrite cannot be combined with --diff or --check")
    if args.json and preview:
        parser.error("--json cannot be combined with --diff or --check")
    symbolic_literals = symbolic_literals_from(parser, args)
    paths = input_paths(parser, args)
    batch = is_batch(args)

    file_results, had_errors = _simplify_files(paths, args, symbolic_literals)

    changed = [(path, res) for path, res in file_results if res.rewritten_source != res.source]
    if preview:
        if args.diff:
            for path, res in changed:
                write_exact(unified_diff(path, res.source, res.rewritten_source))
        if args.check:
            for path, _ in changed:
                print(f"would rewrite {path}", file=sys.stderr)
    elif args.json:
        _write_json(file_results, args, batch=batch, symbolic_literals=symbolic_literals)
    else:
        _write_reports(file_results, args, batch=batch)

    if had_errors:
        return EXIT_ERROR
    if args.check and changed:
        return EXIT_FINDINGS
    has_findings = any(res.has_findings for _, res in file_results)
    return EXIT_FINDINGS if args.fail_on_findings and has_findings else EXIT_OK
