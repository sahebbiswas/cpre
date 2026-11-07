"""``cpre simplify-blocks``: apply, preview or check the suggested conditional-block edits."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .._types import AnalysisOptions, Finding, FindingKind
from ..block_rewrite import DEFAULT_REWRITE_KINDS, BlockRewriteResult, rewrite_blocks
from ..configuration import MacroConfiguration
from ..errors import CpreError
from ..serialization import batch_document, top_level
from ..text_reports import format_error, format_incomplete, unified_diff
from ._common import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_OK,
    add_limit_options,
    add_presentation_options,
    analysis_options_from,
    input_paths,
    is_batch,
    is_stdin,
    progress_for,
    read_source,
    report_failure,
    source_argument,
    write_exact,
)
from ._progress import paused
from .preprocess_configuration import (
    add_configuration_options,
    configuration_from_arguments,
    require_seed_for_seed_options,
)


@dataclass
class _Outcome:
    path: Path
    result: BlockRewriteResult


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Apply the exact edits suggested for mergeable, complementary and joinable "
            "blocks, empty blocks and empty branches, and check the result by analyzing it."
        ),
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=source_argument,
        help="C/C++ source files, directories used with --recursive, or - for standard input",
    )
    parser.add_argument("--recursive", action="store_true", help="scan directories recursively")
    parser.add_argument(
        "--rewrite",
        "--in-place",
        dest="rewrite",
        action="store_true",
        help="rewrite source files in place",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="print the rewrite as a unified diff; never modifies files",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit with status 1 if --rewrite would change any file, naming each on stderr",
    )
    parser.add_argument("--json", action="store_true", help="write the results as JSON")
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit with status 1 when any block edit applies (the report mode, or --rewrite)",
    )
    parser.add_argument(
        "--collapse-nesting",
        action="store_true",
        help="also apply the collapsible-nesting suggestion (a style change, off by default)",
    )
    parser.add_argument(
        "--strict-merge",
        action="store_true",
        help="assume names that are not defined may depend on anything a block body changes",
    )
    add_limit_options(parser)
    add_presentation_options(parser)
    add_configuration_options(parser, analysis=True)
    return parser


def _record(outcome: _Outcome, *, diff: str | None, written: bool) -> dict[str, object]:
    res = outcome.result
    return {
        "path": str(outcome.path),
        "would_change": res.changed,
        "rewritten": written,
        "rounds": res.rounds,
        "applied": [_applied(finding) for finding in res.applied],
        "remaining": len(res.remaining),
        "diff": diff if res.changed else None,
    }


def _applied(finding: Finding) -> dict[str, object]:
    return {"kind": finding.kind.value, "line": finding.location.line}


def _process(
    path: Path,
    args: argparse.Namespace,
    options: AnalysisOptions,
    kinds: frozenset[FindingKind],
    configuration: MacroConfiguration | None,
) -> _Outcome | None:
    try:
        source = read_source(path, newline="")
        res = rewrite_blocks(
            source,
            filename=str(path),
            kinds=kinds,
            options=options,
            configuration=configuration,
            strict_merge=args.strict_merge,
        )
        if not res.complete:
            for diagnostic in res.incomplete:
                report_failure(path, format_incomplete(diagnostic))
            return None
        if args.rewrite and res.changed:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(res.rewritten_source)
        return _Outcome(path, res)
    except CpreError as error:
        report_failure(path, format_error(error))
    except (OSError, UnicodeDecodeError) as error:
        report_failure(path, str(error))
    return None


def _print_report(outcomes: list[_Outcome], changed: list[_Outcome]) -> None:
    """The default output: what ``--rewrite`` would apply, file by file."""
    for outcome in changed:
        edits = ", ".join(
            f"{finding.kind.value} (line {finding.location.line})"
            for finding in outcome.result.applied
        )
        print(f"{outcome.path}: would apply {len(outcome.result.applied)} edit(s): {edits}")
    if changed:
        print("Use --diff to preview the changes or --rewrite to apply them.")
    elif outcomes:
        print("No block edits to apply.")


def simplify_blocks_main(
    argv: Sequence[str] | None = None, prog: str = "cpre simplify-blocks"
) -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    preview = args.diff or args.check
    if args.rewrite and preview:
        parser.error("--rewrite cannot be combined with --diff or --check")
    if args.rewrite and any(is_stdin(path) for path in args.sources):
        parser.error(
            "standard input cannot be rewritten in place; use --diff or --check, or name a file"
        )
    require_seed_for_seed_options(parser, args)
    options = analysis_options_from(parser, args)
    configuration: MacroConfiguration | None = None
    if args.defines or args.undefined or args.config_from:
        try:
            built = configuration_from_arguments(args)
        except ValueError as error:
            parser.error(str(error))
        if built is None:
            return EXIT_ERROR
        configuration = built[0]
    kinds = DEFAULT_REWRITE_KINDS | (
        {FindingKind.COLLAPSIBLE_NESTING} if args.collapse_nesting else frozenset()
    )
    batch = is_batch(args)
    outcomes: list[_Outcome] = []
    had_errors = False
    with progress_for(args, batch=batch) as progress:
        paths = input_paths(parser, args, progress)
        progress.begin(paths)
        for path in paths:
            progress.file_started(path)
            outcome = _process(path, args, options, frozenset(kinds), configuration)
            had_errors = had_errors or outcome is None
            if outcome is not None:
                outcomes.append(outcome)
                if args.diff and outcome.result.changed and not args.json:
                    with paused():
                        write_exact(
                            unified_diff(
                                path, outcome.result.source, outcome.result.rewritten_source
                            )
                        )
            progress.file_done(
                len(outcome.result.applied) if outcome is not None else 0,
                failed=outcome is None,
            )

    changed = [outcome for outcome in outcomes if outcome.result.changed]
    if args.json:
        records = [
            _record(
                outcome,
                diff=(
                    unified_diff(
                        outcome.path, outcome.result.source, outcome.result.rewritten_source
                    )
                    if args.diff
                    else None
                ),
                written=args.rewrite and outcome.result.changed,
            )
            for outcome in outcomes
            if batch is False or outcome.result.changed
        ]
        if batch:
            print(json.dumps(batch_document("simplify-blocks", records), indent=2))
        elif records:
            print(json.dumps(top_level("simplify-blocks", records[0]), indent=2))
    elif args.rewrite:
        for outcome in changed:
            print(f"rewrote {outcome.path} ({len(outcome.result.applied)} edits)")
        if not changed and not had_errors:
            print("No block edits to apply.")
    elif not preview:
        _print_report(outcomes, changed)
    if args.check:
        for outcome in changed:
            print(f"would rewrite {outcome.path}", file=sys.stderr)
    if had_errors:
        return EXIT_ERROR
    if (args.check or args.fail_on_findings) and changed:
        return EXIT_FINDINGS
    return EXIT_OK
