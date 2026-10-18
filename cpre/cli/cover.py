"""``cpre cover``: configurations that together select every reachable branch outcome."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

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
from ..text_reports import format_error, format_incomplete, include_hint, pragma_hint
from ._common import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_OK,
    add_limit_options,
    analysis_options_from,
    read_source,
    source_argument,
)
from .has_include_answers import answer_provider, parse_has_include_answers
from .pragma_options import add_pragma_options, pragma_handler_from
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
    parser.add_argument(
        "source", type=source_argument, help="the C/C++ source file, or - for standard input"
    )
    parser.add_argument(
        "--standard-macro",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a deterministic value for a supported standard predefined macro",
    )
    add_include_options(parser)
    add_pragma_options(parser)
    add_limit_options(parser)
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
    pragma_handler = pragma_handler_from(parser, args)

    try:
        standard: dict[str, str] = {}
        for value in args.standard_macro:
            name, replacement = parse_name_value(value, "--standard-macro")
            if not replacement:
                raise ValueError("--standard-macro requires NAME=VALUE")
            standard[name] = replacement
        context = PreprocessingContext(standard_macros=standard) if standard else None
        options = analysis_options_from(parser, args)
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
        source = read_source(path, newline="")
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
            pragma_handler=pragma_handler,
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
        assert result.outcomes is not None
        hints: list[str] = []
        for outcome in result.outcomes:
            hint = include_hint(outcome.reason or "") or pragma_hint(outcome.reason or "")
            if hint is not None and hint not in hints:
                hints.append(hint)
        for hint in hints:
            print(f"{path}: hint: {hint}", file=sys.stderr)
    else:
        message = format_incomplete(result.incomplete)
        print(f"{path}: {message}", file=sys.stderr)
        for hint in (include_hint(message), pragma_hint(message)):
            if hint is not None:
                print(f"{path}: hint: {hint}", file=sys.stderr)
    if result.incomplete is not None:
        return EXIT_ERROR
    assert result.outcomes is not None
    uncovered = any(o.status is BranchOutcomeStatus.NOT_COVERED for o in result.outcomes)
    return EXIT_FINDINGS if uncovered else EXIT_OK
