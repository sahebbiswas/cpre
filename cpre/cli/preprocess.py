"""``cpre preprocess``: select one concrete configuration and emit the transformed source."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ..configuration import UnknownNamePolicy
from ..errors import CpreError
from ..includes import DEFAULT_MAX_INCLUDE_DEPTH, SearchPathResolver
from ..macro_environment import MacroEnvironmentEntry, macro_environment
from ..pragmas import preprocess_source
from ..preprocessing import PreprocessDiagnostic, PreprocessResult, compact
from ..serialization import (
    macro_environment_document,
    preprocess_document,
    unknown_macros_document,
)
from ..text_reports import format_incomplete, include_hint, pragma_hint, unknown_macro_hint
from ..unknown_macros import UnknownMacro, unknown_macros_in_source
from ._common import EXIT_ERROR, EXIT_OK, read_source, source_argument
from .has_include_answers import answer_provider, parse_has_include_answers
from .pragma_options import add_pragma_options, pragma_handler_from
from .preprocess_configuration import configuration_with_origins


def build_parser(prog: str) -> argparse.ArgumentParser:
    """Build the argument parser for the concrete preprocessing command."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Select one concrete preprocessing configuration and emit the transformed source."
        ),
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=source_argument,
        help="a C/C++ source file, or - for standard input; directory inputs are not supported",
    )
    _add_configuration_options(parser)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit the explicit compact presentation of the successful canonical result",
    )
    parser.add_argument(
        "--max-blank-lines",
        type=int,
        default=0,
        metavar="N",
        help=("with --compact, retain at most N preprocessing-created blank lines (default: 0)"),
    )
    add_include_options(parser)
    add_pragma_options(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "write a JSON document with the output source, structured diagnostics "
            "(including unresolved macro names), and skipped includes to stdout"
        ),
    )
    parser.add_argument(
        "--list-unknown-macros",
        action="store_true",
        help=(
            "instead of preprocessing, list the macros the source's conditionals need "
            "that the configuration does not fix, one tab-separated NAME, USES, LINES "
            "record per line sorted by name; combine with --json for a JSON document"
        ),
    )
    parser.add_argument(
        "--dump-macros",
        action="store_true",
        help=(
            "instead of preprocessing, print cpre's configured macro environment (from "
            "-D/-U, --config-from and --standard-macro) as one tab-separated NAME, STATE, "
            "ORIGIN, REPLACEMENT record per line sorted by name; combine with --json for a "
            "JSON document. This is not a compiler's predefined-macro catalog, and the "
            "source's own #define lines are not included"
        ),
    )
    parser.epilog = (
        "example: cpre preprocess --unknown-names undefined --config-from flags.h "
        "-D LEVEL=2 target.c "
        "(flags.h macros seed the configuration; LEVEL=2 overrides any seed value); "
        "use --config-from-unknown-names undefined instead of --unknown-names undefined "
        "to read the seed closed-world while the target stays open-world"
    )
    return parser


def _add_configuration_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--define",
        "-D",
        dest="defines",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="define an external object-like macro; NAME alone defines an empty macro",
    )
    parser.add_argument(
        "--undef",
        "-U",
        dest="undefined",
        action="append",
        default=[],
        metavar="NAME",
        help="explicitly mark an external macro as undefined",
    )
    parser.add_argument(
        "--config-from",
        dest="config_from",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help=(
            "derive the initial macro configuration from the final macro state of a seed "
            "file such as flags.h; repeatable, applied in order with the last definition "
            "winning; -D/-U are visible while seeds are evaluated and always take precedence"
        ),
    )
    parser.add_argument(
        "--config-exclude",
        dest="config_exclude",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "remove NAME from the configuration derived by --config-from (a private helper "
            "macro or a guard the detector does not recognize); repeatable; the seed still "
            "sees its own definition, and -D/-U of the same name still win"
        ),
    )
    parser.add_argument(
        "--unknown-names",
        choices=[policy.value for policy in UnknownNamePolicy],
        default=UnknownNamePolicy.OPEN.value,
        help=("policy for names absent from the external configuration (default: open)"),
    )
    parser.add_argument(
        "--config-from-unknown-names",
        dest="config_from_unknown_names",
        choices=[policy.value for policy in UnknownNamePolicy],
        default=None,
        help=(
            "policy for names absent from the configuration while --config-from seeds are "
            "evaluated (default: the --unknown-names policy); 'undefined' reads a guarded "
            "flags header with normal C semantics without making the target closed-world"
        ),
    )
    parser.add_argument(
        "--standard-macro",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a deterministic value for a supported standard predefined macro",
    )


def add_include_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-includes",
        action="store_true",
        help=(
            "mask active #include/#include_next/#import directives instead of failing; "
            "skipped headers are not read and their macros are not assumed; with -I or "
            "--iquote, only includes that cannot be resolved are skipped"
        ),
    )
    parser.add_argument(
        "-I",
        "--include-dir",
        dest="include_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help=(
            "resolve and preprocess reachable includes, searching DIR for <...> and "
            '"..." headers; repeatable, searched in order'
        ),
    )
    parser.add_argument(
        "--iquote",
        dest="quote_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help=(
            'resolve and preprocess reachable includes, searching DIR for "..." headers '
            "only; repeatable, searched before -I directories"
        ),
    )
    parser.add_argument(
        "--has-include-from-search",
        action="store_true",
        help=(
            "answer reachable __has_include(...) from the -I/--iquote search: a header is "
            "available exactly when the same #include would resolve; requires -I or --iquote"
        ),
    )
    parser.add_argument(
        "--has-include",
        dest="has_include",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "answer one __has_include query explicitly: '<opt.h>=1' (available) or "
            "'\"local.h\"=0' (unavailable); angle and quoted forms are separate; repeatable; "
            "explicit answers win over --has-include-from-search, which answers the rest"
        ),
    )
    parser.add_argument(
        "--max-include-depth",
        type=int,
        default=DEFAULT_MAX_INCLUDE_DEPTH,
        metavar="N",
        help=f"maximum nesting depth of resolved includes (default: {DEFAULT_MAX_INCLUDE_DEPTH})",
    )


def _print_unknown_macros(path: Path, unknown: Sequence[UnknownMacro], *, as_json: bool) -> int:
    """Write ``--list-unknown-macros`` output; records are sorted by name."""
    if as_json:
        print(json.dumps(unknown_macros_document(path, unknown), indent=2))
        return EXIT_OK
    for item in unknown:
        uses = ",".join(use.value for use in item.uses)
        lines = ",".join(str(location.line) for location in item.locations)
        print(f"{item.name}\t{uses}\t{lines}")
    return EXIT_OK


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _print_macro_environment(
    path: Path,
    entries: Sequence[MacroEnvironmentEntry],
    *,
    unknown_names: str,
    as_json: bool,
) -> int:
    """Write ``--dump-macros`` output; records are sorted by name."""
    if as_json:
        document = macro_environment_document(path, entries, unknown_names=unknown_names)
        print(json.dumps(document, indent=2))
        return EXIT_OK
    for entry in entries:
        name = entry.name
        if entry.parameters is not None:
            parameters = [*entry.parameters, *(["..."] if entry.variadic else [])]
            name = f"{name}({','.join(parameters)})"
        origin = f"seed:{_escape(entry.path)}" if entry.path is not None else entry.origin
        state = "defined" if entry.defined else "undefined"
        replacement = _escape(entry.replacement) if entry.replacement is not None else ""
        print(f"{name}\t{state}\t{origin}\t{replacement}")
    return EXIT_OK


def _check_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    """Reject option combinations the command does not support; return the one source."""
    if args.list_unknown_macros and args.compact:
        parser.error("--compact cannot be combined with --list-unknown-macros")
    if args.dump_macros and (args.compact or args.list_unknown_macros):
        parser.error("--dump-macros cannot be combined with --compact or --list-unknown-macros")
    if args.has_include_from_search and not (args.include_dirs or args.quote_dirs):
        parser.error("--has-include-from-search requires -I or --iquote")
    if args.config_exclude and not args.config_from:
        parser.error("--config-exclude requires --config-from")
    if args.config_from_unknown_names is not None and not args.config_from:
        parser.error("--config-from-unknown-names requires --config-from")
    if len(args.sources) != 1:
        parser.error("preprocess currently accepts exactly one source file")
    path: Path = args.sources[0]
    if path.is_dir():
        parser.error("directory inputs are not supported by cpre preprocess")
    return path


def _write_json(path: Path, result: PreprocessResult, args: argparse.Namespace) -> int:
    output_source = result.source
    if result.complete and args.compact:
        try:
            output_source = compact(result, max_consecutive_blank_lines=args.max_blank_lines)
        except ValueError as error:
            print(f"{path}: {error}", file=sys.stderr)
            return EXIT_ERROR
    print(json.dumps(preprocess_document(path, result, output_source), indent=2))
    return EXIT_OK if result.complete else EXIT_ERROR


def _write_diagnostics(path: Path, result: PreprocessResult) -> None:
    for diagnostic in result.incomplete:
        origin = diagnostic.source_identity or path
        print(f"{origin}: {format_incomplete(diagnostic)}", file=sys.stderr)
        for hint in (include_hint(diagnostic.message), pragma_hint(diagnostic.message)):
            if hint is not None:
                print(f"{origin}: hint: {hint}", file=sys.stderr)
        if isinstance(diagnostic, PreprocessDiagnostic):
            for item in diagnostic.unresolved:
                print(f"{origin}: hint: {unknown_macro_hint(item)}", file=sys.stderr)


def _write_source(path: Path, result: PreprocessResult, args: argparse.Namespace) -> int:
    for skipped in result.skipped_includes:
        origin = skipped.source_identity or path
        print(
            f"{origin}: line {skipped.location.line}: "
            f"skipped #{skipped.directive} {skipped.operand}",
            file=sys.stderr,
        )

    assert result.source is not None
    if args.compact:
        try:
            output = compact(result, max_consecutive_blank_lines=args.max_blank_lines)
        except ValueError as error:
            print(f"{path}: {error}", file=sys.stderr)
            return EXIT_ERROR
    else:
        output = result.source

    sys.stdout.buffer.write(output.encode("utf-8"))
    return EXIT_OK


def preprocess_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre preprocess",
) -> int:
    """Run the concrete preprocessing CLI workflow for one source file."""
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    path = _check_options(parser, args)
    pragma_handler = pragma_handler_from(parser, args)

    try:
        built = configuration_with_origins(args)
        if built is None:
            return EXIT_ERROR
        configuration, context, origins = built
        if args.dump_macros:
            return _print_macro_environment(
                path,
                macro_environment(configuration, context, origins),
                unknown_names=configuration.unknown_names.value,
                as_json=args.json,
            )
        # Validated before any early return, so a malformed answer always fails.
        include_query = (
            answer_provider(parse_has_include_answers(args.has_include))
            if args.has_include
            else None
        )
        source = read_source(path, newline="")
        if args.list_unknown_macros:
            unknown = unknown_macros_in_source(
                source, filename=str(path), configuration=configuration, context=context
            )
            return _print_unknown_macros(path, unknown, as_json=args.json)
        resolver = (
            SearchPathResolver(args.include_dirs, quote_paths=args.quote_dirs)
            if args.include_dirs or args.quote_dirs
            else None
        )
        result = preprocess_source(
            source,
            filename=str(path),
            configuration=configuration,
            context=context,
            skip_includes=args.skip_includes,
            include_resolver=resolver,
            max_include_depth=args.max_include_depth,
            include_query=include_query,
            has_include_from_resolver=args.has_include_from_search,
            pragma_handler=pragma_handler,
        )
    except (CpreError, ValueError, OSError, UnicodeDecodeError) as error:
        origin = getattr(error, "filename", None) or path
        print(f"{origin}: {getattr(error, 'message', str(error))}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        return _write_json(path, result, args)
    if not result.complete:
        _write_diagnostics(path, result)
        return EXIT_ERROR
    return _write_source(path, result, args)
