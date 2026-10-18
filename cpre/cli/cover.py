"""``cpre cover``: configurations that together select every reachable branch outcome."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from .._types import AnalysisOptions
from ..coverage import (
    BranchCoverageResult,
    BranchOutcome,
    BranchOutcomeStatus,
    CoverageConfiguration,
    cover_branches,
)
from ..errors import CpreError
from ..includes import DEFAULT_MAX_INCLUDE_DEPTH, SearchPathResolver
from ..preprocessing import PreprocessingContext
from ..serialization import cover_document
from ..text_reports import format_error, format_incomplete
from ._common import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK
from .has_include_answers import answer_provider, parse_has_include_answers
from .preprocess import add_include_options
from .preprocess_configuration import parse_name_value


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Generate concrete macro configurations that together select every reachable "
            "conditional-branch outcome of one source file."
        ),
    )
    parser.add_argument("source", type=Path, help="the C/C++ source file")
    parser.add_argument(
        "--standard-macro",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a deterministic value for a supported standard predefined macro",
    )
    add_include_options(parser)
    parser.add_argument(
        "--max-atoms", type=int, metavar="N", help="resource limit per independent component"
    )
    parser.add_argument(
        "--max-bdd-nodes", type=int, metavar="N", help="resource limit per independent component"
    )
    parser.add_argument(
        "--max-work", type=int, metavar="N", help="resource limit shared by all components"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write configurations, required assignments, don't-cares and outcomes as JSON",
    )
    return parser


def configuration_flags(item: CoverageConfiguration) -> list[str]:
    """The ``cpre preprocess`` arguments that select this configuration.

    Each configuration is closed-world, so ``--unknown-names undefined`` comes first;
    then one ``-D NAME=VALUE`` or ``-U NAME`` per macro, sorted by name.
    """
    settings = {
        definition.name: f"-D {definition.name}={definition.replacement}"
        for definition in item.configuration.definitions
    }
    for name in item.configuration.undefined:
        settings[name] = f"-U {name}"
    flags = ["--unknown-names", "undefined"]
    for name in sorted(settings):
        flags.extend(settings[name].split(" ", 1))
    return flags


def _outcome_label(outcome: BranchOutcome) -> str:
    if outcome.directive is None:
        return f"line {outcome.group_line}: no branch selected"
    return f"line {outcome.branch_line}: #{outcome.directive}"


def _write_text(result: BranchCoverageResult) -> None:
    assert result.configurations is not None and result.outcomes is not None
    for item in result.configurations:
        print(shlex.join(configuration_flags(item)))
    for outcome in result.outcomes:
        if outcome.status is BranchOutcomeStatus.COVERED:
            continue
        status = outcome.status.value.replace("_", " ")
        print(f"# {status}: {_outcome_label(outcome)}: {outcome.reason}")


def cover_main(argv: Sequence[str] | None = None, prog: str = "cpre cover") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    path: Path = args.source
    if path.is_dir():
        parser.error("directory inputs are not supported by cpre cover")
    if args.has_include_from_search and not (args.include_dirs or args.quote_dirs):
        parser.error("--has-include-from-search requires -I or --iquote")

    try:
        standard: dict[str, str] = {}
        for value in args.standard_macro:
            name, replacement = parse_name_value(value, "--standard-macro")
            if not replacement:
                raise ValueError("--standard-macro requires NAME=VALUE")
            standard[name] = replacement
        context = PreprocessingContext(standard_macros=standard) if standard else None
        limits = {
            key: value
            for key, value in (
                ("max_atoms", args.max_atoms),
                ("max_bdd_nodes", args.max_bdd_nodes),
                ("max_work", args.max_work),
            )
            if value is not None
        }
        options = AnalysisOptions(**limits)
        include_query = (
            answer_provider(parse_has_include_answers(args.has_include))
            if args.has_include
            else None
        )
        resolver = (
            SearchPathResolver(args.include_dirs, quote_paths=args.quote_dirs)
            if args.include_dirs or args.quote_dirs
            else None
        )
        with path.open("r", encoding="utf-8", newline="") as handle:
            source = handle.read()
        result = cover_branches(
            source,
            filename=str(path),
            context=context,
            options=options,
            include_query=include_query,
            skip_includes=args.skip_includes,
            include_resolver=resolver,
            max_include_depth=args.max_include_depth,
            has_include_from_resolver=args.has_include_from_search,
        )
    except CpreError as error:
        print(f"{path}: {format_error(error)}", file=sys.stderr)
        return EXIT_ERROR
    except (ValueError, OSError, UnicodeDecodeError) as error:
        print(f"{path}: {getattr(error, 'message', str(error))}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(cover_document(path, result, flags=configuration_flags), indent=2))
    elif result.incomplete is None:
        _write_text(result)
    else:
        print(f"{path}: {format_incomplete(result.incomplete)}", file=sys.stderr)
    if result.incomplete is not None:
        return EXIT_ERROR
    assert result.outcomes is not None
    uncovered = any(o.status is BranchOutcomeStatus.NOT_COVERED for o in result.outcomes)
    return EXIT_FINDINGS if uncovered else EXIT_OK
