"""Command-line translation layer for cpre."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__, cpre as _engine
from .api import AnalysisIncomplete, AnalysisResult, CpreError, ErrorCode, analyze_source
from .sarif import ToolNotification, sarif_log


def _format_error(error: CpreError) -> str:
    if error.location is None:
        return error.message
    if error.location.column is None:
        return f"line {error.location.line}: {error.message}"
    return f"{error.message} at line {error.location.line}, column {error.location.column}"


def _format_incomplete(diagnostic: AnalysisIncomplete) -> str:
    if diagnostic.location is None:
        return diagnostic.message
    return f"line {diagnostic.location.line}: {diagnostic.message}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Boolean C/C++ preprocessor conditional directives."
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
        "--json", action="store_true", help="write the conditional tree as JSON"
    )
    output_group.add_argument(
        "--sarif", action="store_true", help="write findings as SARIF 2.1.0"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include unchanged conditional branches in the report",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit with status 1 when a dead or redundant branch is found",
    )
    args = parser.parse_args(argv)

    try:
        paths = _engine._source_paths(args.sources, args.recursive)
    except _engine.ConditionError as error:
        parser.error(str(error))

    results: list[tuple[Path, _engine.ConditionalTree]] = []
    analyses: list[AnalysisResult] = []
    sarif_notifications: list[ToolNotification] = []
    had_errors = False
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            result = analyze_source(source, filename=str(path))
            analyses.append(result)
            if not result.complete:
                for diagnostic in result.incomplete:
                    print(f"{path}: {_format_incomplete(diagnostic)}", file=sys.stderr)
                had_errors = True
                continue
            results.append((path, result.tree))
        except CpreError as error:
            print(f"{path}: {_format_error(error)}", file=sys.stderr)
            sarif_notifications.append(
                ToolNotification(
                    message=error.message,
                    descriptor_id=error.code,
                    filename=str(path),
                    location=error.location,
                )
            )
            had_errors = True
        except (OSError, UnicodeDecodeError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            sarif_notifications.append(
                ToolNotification(
                    message=str(error),
                    descriptor_id=ErrorCode.SOURCE_READ_ERROR,
                    filename=str(path),
                )
            )
            had_errors = True

    batch_mode = len(args.sources) > 1 or any(path.is_dir() for path in args.sources)
    if args.sarif:
        print(
            json.dumps(
                sarif_log(
                    analyses,
                    tool_version=__version__,
                    notifications=sarif_notifications,
                ),
                indent=2,
            )
        )
    elif args.json:
        if batch_mode:
            files = []
            for path, tree in results:
                tree_dict = _engine.tree_to_dict(tree, verbose=args.verbose)
                if args.verbose or tree_dict["groups"]:
                    files.append({"path": str(path), **tree_dict})
            output = {"files": files}
        elif results:
            output = _engine.tree_to_dict(results[0][1], verbose=args.verbose)
        else:
            output = None
        if output is not None:
            print(json.dumps(output, indent=2))
    elif batch_mode:
        color = sys.stdout.isatty()
        reports = []
        for path, tree in results:
            report, has_entries = _engine._render_report(
                tree, verbose=args.verbose, color=color
            )
            if args.verbose or has_entries:
                reports.append(
                    "\n".join(
                        (_engine._colored(f"== {path} ==", "cyan", color), report)
                    )
                )
        if reports:
            print("\n\n".join(reports))
    elif results:
        print(
            _engine.format_report(
                results[0][1], verbose=args.verbose, color=sys.stdout.isatty()
            )
        )

    if had_errors:
        return 2
    has_findings = any(_engine._has_findings(tree) for _, tree in results)
    return 1 if args.fail_on_findings and has_findings else 0
