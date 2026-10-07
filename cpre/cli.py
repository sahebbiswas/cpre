"""Command-line translation layer for cpre."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from . import cpre as _engine
from .api import (
    AnalysisError,
    AnalysisIncomplete,
    AnalysisOptions,
    AnalysisResult,
    CpreError,
    ErrorCode,
    IncompleteComponent,
    analyze_source,
)
from .configuration import MacroConfiguration, UnknownNamePolicy
from .expressions import (
    expression_atoms_in_order,
    expressions_differ,
    format_expression,
    parse_expression,
)
from .includes import DEFAULT_MAX_INCLUDE_DEPTH, SearchPathResolver
from .macro_analysis import (
    MacroAnalysisResult,
    MacroSimplificationResult,
    _resolve_symbolic_literals,
    _symbolize,
    analyze_macros,
    simplify_macros,
)
from .macros import MacroDefinition
from .model import TRUE, ConditionError, ExpressionSyntaxError, Predicate
from .pragmas import preprocess_source
from .preprocessing import PreprocessingContext, PreprocessResult, compact
from .robdd import (
    BDD,
    AnalysisBudget,
    AnalysisLimitExceeded,
    exact_simplify,
)
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


def _group_span(component: IncompleteComponent) -> str:
    return ", ".join(
        f"{group.location.line}-{group.end_line}"
        if group.end_line is not None and group.end_line != group.location.line
        else str(group.location.line)
        for group in component.groups
    )


def _format_incomplete_component(component: IncompleteComponent) -> str:
    """One stderr line per incomplete component of an otherwise analyzed source."""
    first = component.groups[0].location.line
    reasons = "; ".join(diagnostic.message for diagnostic in component.diagnostics)
    noun = "conditionals" if len(component.groups) > 1 else "conditional"
    return (
        f"line {first}: {noun} at lines {_group_span(component)} "
        f"not analyzed ({reasons}); findings cover only the rest of the file"
    )


def _incomplete_lines(
    components: Sequence[IncompleteComponent], *, color: bool = False
) -> list[str]:
    if not components:
        return []
    lines = [
        _engine._colored(
            "Incomplete analysis (no findings are reported for these conditionals):",
            "yellow",
            color,
        )
    ]
    for component in components:
        reasons = "; ".join(diagnostic.message for diagnostic in component.diagnostics)
        for group in component.groups:
            span = (
                f"{group.location.line}-{group.end_line}"
                if group.end_line is not None
                else str(group.location.line)
            )
            condition = f" {group.condition}" if group.condition else ""
            lines.append(
                _engine._colored(
                    f"  {span}: #{group.directive}{condition} [incomplete: {reasons}]",
                    "yellow",
                    color,
                )
            )
    return lines


def _incomplete_to_dict(diagnostic: AnalysisIncomplete) -> dict[str, object]:
    return {
        "code": diagnostic.code.value,
        "resource": diagnostic.resource,
        "limit": diagnostic.limit,
        "observed": diagnostic.observed,
        "message": diagnostic.message,
        "line": diagnostic.location.line if diagnostic.location else None,
        "component": diagnostic.component,
    }


def _incomplete_component_to_dict(component: IncompleteComponent) -> dict[str, object]:
    return {
        "index": component.index,
        "groups": [
            {
                "line": group.location.line,
                "end_line": group.end_line,
                "directive": group.directive,
                "condition": group.condition,
            }
            for group in component.groups
        ],
        "atoms": list(component.atoms),
        "diagnostics": [_incomplete_to_dict(d) for d in component.diagnostics],
    }


def _analysis_status_dict(analysis: AnalysisResult) -> dict[str, object]:
    return {
        "complete": analysis.complete,
        "partial": analysis.partial,
        "incomplete": [_incomplete_to_dict(d) for d in analysis.incomplete],
        "incomplete_components": [
            _incomplete_component_to_dict(c) for c in analysis.incomplete_components
        ],
    }


def _macro_to_dict(result: MacroAnalysisResult) -> dict[str, object]:
    return {
        "name": result.name,
        "line": result.location.line if result.location else None,
        "column": result.location.column if result.location else None,
        "candidate": result.candidate,
        "simplified": result.simplified,
        "original": result.original_replacement,
        "replacement": result.simplified_replacement,
        "equivalent": result.is_equivalent,
        "reason": result.reason,
        "semantics": result.semantics,
        "symbolic_literals": list(result.symbolic_literals),
    }


def _render_macro_report(
    results: Sequence[MacroAnalysisResult],
    *,
    verbose: bool = True,
    color: bool = False,
    rewritten: bool = False,
) -> tuple[str, bool]:
    lines: list[str] = []
    has_entries = False
    for r in results:
        loc = f"line {r.location.line}: " if r.location and r.location.line else ""
        if r.simplified:
            has_entries = True
            tag = "rewritten, proven equivalent" if rewritten else "proven equivalent"
            line = (
                f"{loc}#define {r.name} {r.original_replacement} -> "
                f"{r.simplified_replacement} [{tag}]"
            )
            lines.append(_engine._colored(line, "green", color))
        elif verbose:
            if r.candidate:
                reason = r.reason or "simplest equivalent form"
                line = f"{loc}#define {r.name} {r.original_replacement} ({reason})"
                lines.append(_engine._colored(line, "gray", color))
            else:
                line = f"{loc}#define {r.name} skipped: {r.reason}"
                lines.append(_engine._colored(line, "gray", color))
    if lines:
        return "\n".join(lines), has_entries
    if results:
        return "No simplifiable macro definitions found.", False
    return "No macro definitions found.", False


def _render_combined_report(
    tree: _engine.ConditionalTree | None,
    macros: Sequence[MacroAnalysisResult],
    *,
    verbose: bool = True,
    color: bool = False,
    incomplete_components: Sequence[IncompleteComponent] = (),
) -> tuple[str, bool]:
    sections: list[str] = []
    has_any = False
    if tree is not None:
        c_report, c_has = _engine._render_report(tree, verbose=verbose, color=color)
        if c_has or (verbose and c_report):
            sections.append(c_report)
            if c_has:
                has_any = True
    if incomplete_components:
        sections.append("\n".join(_incomplete_lines(incomplete_components, color=color)))
        has_any = True
    if macros:
        m_report, m_has = _render_macro_report(macros, verbose=verbose, color=color)
        if m_has or (verbose and m_report):
            sections.append(m_report)
            if m_has:
                has_any = True
    if sections:
        return "\n".join(sections), has_any
    if tree is not None and tree.groups:
        return "No notable conditional directives or simplifiable macros found.", False
    if macros:
        return "No notable conditional directives or simplifiable macros found.", False
    return "No conditional directives or macro definitions found.", False


def _build_file_json(
    path: Path | str,
    tree: _engine.ConditionalTree | None,
    m_results: Sequence[MacroAnalysisResult],
    *,
    verbose: bool,
    enable_macros: bool,
    symbolic_literals: tuple[int, ...],
    analysis: AnalysisResult | None = None,
) -> tuple[dict[str, object], bool]:
    f_data: dict[str, object] = {"path": str(path)}
    has_visible = False
    if tree is not None:
        t_dict = _engine.tree_to_dict(tree, verbose=verbose)
        f_data["groups"] = t_dict["groups"]
        if t_dict["groups"]:
            has_visible = True
    else:
        f_data["groups"] = []
    if analysis is not None:
        f_data.update(_analysis_status_dict(analysis))
        if not analysis.complete:
            has_visible = True

    if enable_macros:
        visible_macros = [_macro_to_dict(r) for r in m_results if (verbose or r.simplified)]
        if any(r.simplified for r in m_results):
            has_visible = True
        f_data["macros"] = visible_macros
    else:
        f_data["macros"] = []

    f_data["semantics"] = "symbolic-literal" if symbolic_literals else "ordinary"
    f_data["symbolic_literals"] = list(symbolic_literals)
    return f_data, has_visible


def _build_simplify_macros_json(
    path: Path | str,
    res: MacroSimplificationResult,
    *,
    verbose: bool,
    symbolic_literals: tuple[int, ...],
) -> tuple[dict[str, object], bool]:
    visible_macros = [_macro_to_dict(r) for r in res.results if (verbose or r.simplified)]
    f_data: dict[str, object] = {
        "path": str(path),
        "rewritten": res.rewritten,
        "applied_count": res.applied_count,
        "verified": res.verified,
        "semantics": "symbolic-literal" if symbolic_literals else "ordinary",
        "symbolic_literals": list(symbolic_literals),
        "macros": visible_macros,
    }
    has_visible = res.has_findings
    return f_data, has_visible


def _build_simplify_macros_parser(prog: str) -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--symbolic-literal",
        dest="symbolic_literals",
        type=int,
        action="append",
        metavar="N",
        help="treat integer literal N as a symbolic Boolean atom in macro analysis (e.g. 0)",
    )
    parser.add_argument(
        "--symbolic-zero",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 0",
    )
    return parser


def simplify_macros_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre simplify-macros",
) -> int:
    parser = _build_simplify_macros_parser(prog)
    args = parser.parse_args(argv)

    symbolic_literals: tuple[int, ...] = ()
    if args.symbolic_zero or args.symbolic_literals:
        raw_literals: list[int] = []
        if args.symbolic_zero:
            raw_literals.append(0)
        if args.symbolic_literals:
            raw_literals.extend(args.symbolic_literals)
        try:
            symbolic_literals = _resolve_symbolic_literals(raw_literals)
        except CpreError as error:
            parser.error(error.message)

    try:
        paths = _engine._source_paths(args.sources, args.recursive)
    except _engine.ConditionError as error:
        parser.error(str(error))

    batch_mode = len(args.sources) > 1 or any(path.is_dir() for path in args.sources)
    file_results: list[tuple[Path, MacroSimplificationResult]] = []
    had_errors = False

    for path in paths:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                source = f.read()
            res = simplify_macros(
                source,
                filename=str(path),
                rewrite=args.rewrite,
                symbolic_literals=symbolic_literals,
            )
            for r in res.results:
                if not r.complete and r.incomplete:
                    print(f"{path}: {_format_incomplete(r.incomplete)}", file=sys.stderr)
                    had_errors = True
            if args.rewrite and res.rewritten:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(res.rewritten_source)
            file_results.append((path, res))
        except CpreError as error:
            print(f"{path}: {_format_error(error)}", file=sys.stderr)
            had_errors = True
        except (OSError, UnicodeDecodeError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            had_errors = True

    if args.json:
        if batch_mode:
            files = []
            for path, res in file_results:
                f_data, has_visible = _build_simplify_macros_json(
                    path, res, verbose=args.verbose, symbolic_literals=symbolic_literals
                )
                if args.verbose or has_visible:
                    files.append(f_data)
            print(json.dumps({"files": files}, indent=2))
        elif file_results:
            f_data, _ = _build_simplify_macros_json(
                file_results[0][0],
                file_results[0][1],
                verbose=args.verbose,
                symbolic_literals=symbolic_literals,
            )
            print(json.dumps(f_data, indent=2))
    elif batch_mode:
        color = sys.stdout.isatty()
        reports = []
        for path, res in file_results:
            report, has_entries = _render_macro_report(
                res.results,
                verbose=args.verbose,
                color=color,
                rewritten=args.rewrite,
            )
            if args.verbose or has_entries:
                reports.append(
                    "\n".join((_engine._colored(f"== {path} ==", "cyan", color), report))
                )
        if reports:
            print("\n\n".join(reports))
    elif file_results:
        report, _ = _render_macro_report(
            file_results[0][1].results,
            verbose=args.verbose,
            color=sys.stdout.isatty(),
            rewritten=args.rewrite,
        )
        print(report)

    if had_errors:
        return 2
    has_findings = any(res.has_findings for _, res in file_results)
    return 1 if args.fail_on_findings and has_findings else 0


def _parse_name_value(value: str, option: str) -> tuple[str, str]:
    """Parse a CLI macro assignment into a name and replacement text."""
    if "=" in value:
        name, replacement = value.split("=", 1)
    else:
        name, replacement = value, ""
    if (
        not name
        or not (name[0].isalpha() or name[0] == "_")
        or not all(ch.isalnum() or ch == "_" for ch in name)
    ):
        raise ValueError(f"{option}: invalid macro name {name!r}")
    return name, replacement


def _build_preprocess_parser(prog: str) -> argparse.ArgumentParser:
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
        type=Path,
        help="C/C++ source files; directory inputs are not supported",
    )
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
        "--unknown-names",
        choices=[policy.value for policy in UnknownNamePolicy],
        default=UnknownNamePolicy.OPEN.value,
        help=("policy for names absent from the external configuration (default: open)"),
    )
    parser.add_argument(
        "--standard-macro",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a deterministic value for a supported standard predefined macro",
    )
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
        "--max-include-depth",
        type=int,
        default=DEFAULT_MAX_INCLUDE_DEPTH,
        metavar="N",
        help=f"maximum nesting depth of resolved includes (default: {DEFAULT_MAX_INCLUDE_DEPTH})",
    )
    return parser


def preprocess_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre preprocess",
) -> int:
    """Run the concrete preprocessing CLI workflow for one source file."""
    parser = _build_preprocess_parser(prog)
    args = parser.parse_args(argv)
    if len(args.sources) != 1:
        parser.error("preprocess currently accepts exactly one source file")
    path = args.sources[0]
    if path.is_dir():
        parser.error("directory inputs are not supported by cpre preprocess")

    try:
        definitions: list[MacroDefinition] = []
        presence: list[str] = []
        for value in args.defines:
            name, replacement = _parse_name_value(value, "--define")
            if replacement:
                definitions.append(MacroDefinition(name, replacement))
            else:
                presence.append(name)

        undefined = []
        for value in args.undefined:
            if "=" in value:
                raise ValueError("--undef accepts only a macro name")
            name, _ = _parse_name_value(value, "--undef")
            undefined.append(name)

        standard_macros: dict[str, str] = {}
        for value in args.standard_macro:
            name, replacement = _parse_name_value(value, "--standard-macro")
            if not replacement:
                raise ValueError("--standard-macro requires NAME=VALUE")
            standard_macros[name] = replacement

        configuration = MacroConfiguration(
            presence=presence,
            undefined=undefined,
            definitions=definitions,
            unknown_names=args.unknown_names,
        )
        context = PreprocessingContext(standard_macros=standard_macros) if standard_macros else None
        with path.open("r", encoding="utf-8", newline="") as handle:
            source = handle.read()
        resolver = (
            SearchPathResolver(args.include_dirs, quote_paths=args.quote_dirs)
            if args.include_dirs or args.quote_dirs
            else None
        )
        result: PreprocessResult = preprocess_source(
            source,
            filename=str(path),
            configuration=configuration,
            context=context,
            skip_includes=args.skip_includes,
            include_resolver=resolver,
            max_include_depth=args.max_include_depth,
        )
    except (CpreError, ValueError, OSError, UnicodeDecodeError) as error:
        origin = getattr(error, "filename", None) or path
        print(f"{origin}: {getattr(error, 'message', str(error))}", file=sys.stderr)
        return 2

    if not result.complete:
        for diagnostic in result.incomplete:
            origin = diagnostic.source_identity or path
            print(f"{origin}: {_format_incomplete(diagnostic)}", file=sys.stderr)
        return 2

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
            return 2
    else:
        output = result.source

    sys.stdout.buffer.write(output.encode("utf-8"))
    return 0


def _build_test_input_parser(prog: str) -> argparse.ArgumentParser:
    """Build the argument parser for testing arbitrary Boolean expressions."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Parse and simplify an arbitrary Boolean expression using ROBDD.",
    )
    parser.add_argument(
        "expression",
        help="Boolean expression to simplify",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the simplified result as JSON",
    )
    parser.add_argument(
        "--symbolic-literal",
        dest="symbolic_literals",
        type=int,
        action="append",
        metavar="N",
        help="treat integer literal N as a symbolic Boolean atom in expression analysis (e.g. 0)",
    )
    parser.add_argument(
        "--symbolic-zero",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 0",
    )
    parser.add_argument(
        "--max-atoms",
        type=int,
        metavar="N",
        help="maximum number of distinct Boolean atoms before aborting (default: 64)",
    )
    parser.add_argument(
        "--max-bdd-nodes",
        type=int,
        metavar="N",
        help="maximum number of BDD nodes before aborting (default: 100000)",
    )
    parser.add_argument(
        "--max-work",
        type=int,
        metavar="N",
        help="maximum deterministic BDD operations before aborting (default: 500000)",
    )
    return parser


def test_input_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre test-input",
) -> int:
    """Parse, simplify, and print an arbitrary Boolean expression."""
    parser = _build_test_input_parser(prog)
    args = parser.parse_args(argv)

    symbolic_literals: tuple[int, ...] = ()
    if args.symbolic_zero or args.symbolic_literals:
        raw_literals: list[int] = []
        if args.symbolic_zero:
            raw_literals.append(0)
        if args.symbolic_literals:
            raw_literals.extend(args.symbolic_literals)
        try:
            symbolic_literals = _resolve_symbolic_literals(raw_literals)
        except CpreError as error:
            parser.error(error.message)

    options_kwargs: dict[str, int] = {}
    if args.max_atoms is not None:
        options_kwargs["max_atoms"] = args.max_atoms
    if args.max_bdd_nodes is not None:
        options_kwargs["max_bdd_nodes"] = args.max_bdd_nodes
    if args.max_work is not None:
        options_kwargs["max_work"] = args.max_work

    try:
        options = AnalysisOptions(**options_kwargs)
    except (AnalysisError, ValueError) as error:
        message = getattr(error, "message", str(error))
        parser.error(message)

    try:
        parsed = parse_expression(args.expression)
    except (ConditionError, CpreError, RecursionError) as error:
        print(f"{prog}: error: {error}", file=sys.stderr)
        return 2

    predicates = [atom for atom in expression_atoms_in_order(parsed) if isinstance(atom, Predicate)]
    if predicates:
        print(f"{prog}: error: unsupported syntax: {predicates[0].text!r}", file=sys.stderr)
        return 2

    expr = _symbolize(parsed, symbolic_literals)

    limits = options._resource_limits()
    budget = AnalysisBudget(limits.max_work)
    atoms = tuple(dict.fromkeys(expression_atoms_in_order(expr)))

    try:
        if len(atoms) > limits.max_atoms:
            raise AnalysisLimitExceeded("atoms", limits.max_atoms, len(atoms))
        bdd = BDD(atoms, limits=limits, budget=budget)
        simplified_expr = exact_simplify(expr, bdd)
        is_equivalent = bdd.equivalent_under(TRUE, expr, simplified_expr)
    except AnalysisLimitExceeded as error:
        print(f"{prog}: error: {error}", file=sys.stderr)
        return 2

    if not is_equivalent:
        print(
            f"{prog}: error: simplified expression could not be proven equivalent",
            file=sys.stderr,
        )
        return 2

    differ = expressions_differ(expr, simplified_expr)
    simplified_str = format_expression(simplified_expr)

    if args.json:
        data = {
            "input": args.expression,
            "simplified": simplified_str,
            "equivalent": is_equivalent,
            "changed": differ,
            "semantics": "symbolic-literal" if symbolic_literals else "ordinary",
            "symbolic_literals": list(symbolic_literals),
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"Input:      {args.expression}")
        print(f"Simplified: {simplified_str}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the cpre command-line interface."""
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in ("simplify-macros", "analyze-macros"):
        return simplify_macros_main(argv[1:], prog=f"cpre {argv[0]}")
    if argv and argv[0] == "preprocess":
        return preprocess_main(argv[1:], prog="cpre preprocess")
    if argv and argv[0] == "test-input":
        return test_input_main(argv[1:], prog="cpre test-input")

    parser = argparse.ArgumentParser(
        description="Analyze Boolean C/C++ preprocessor conditional directives and macros."
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
    parser.add_argument(
        "--symbolic-literal",
        dest="symbolic_literals",
        type=int,
        action="append",
        metavar="N",
        help="treat integer literal N as a symbolic Boolean atom in macro analysis (e.g. 0)",
    )
    parser.add_argument(
        "--symbolic-zero",
        action="store_true",
        help="convenience shorthand for --symbolic-literal 0",
    )
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
    args = parser.parse_args(argv)

    symbolic_literals: tuple[int, ...] = ()
    if args.symbolic_zero or args.symbolic_literals:
        raw_literals: list[int] = []
        if args.symbolic_zero:
            raw_literals.append(0)
        if args.symbolic_literals:
            raw_literals.extend(args.symbolic_literals)
        try:
            symbolic_literals = _resolve_symbolic_literals(raw_literals)
        except CpreError as error:
            parser.error(error.message)

    try:
        paths = _engine._source_paths(args.sources, args.recursive)
    except _engine.ConditionError as error:
        parser.error(str(error))

    batch_mode = len(args.sources) > 1 or any(path.is_dir() for path in args.sources)
    enable_conditionals = not args.no_conditionals
    enable_macros = not args.no_macros

    file_results: list[
        tuple[
            Path,
            _engine.ConditionalTree | None,
            tuple[MacroAnalysisResult, ...],
            AnalysisResult | None,
        ]
    ] = []
    analyses: list[AnalysisResult] = []
    sarif_notifications: list[ToolNotification] = []
    had_errors = False

    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            tree: _engine.ConditionalTree | None = None
            c_result: AnalysisResult | None = None
            if enable_conditionals:
                c_result = analyze_source(source, filename=str(path))
                analyses.append(c_result)
                if not c_result.complete:
                    for diagnostic in c_result.incomplete:
                        if diagnostic.component is None:
                            message = _format_incomplete(diagnostic)
                        else:
                            component = c_result.incomplete_components[diagnostic.component]
                            message = _format_incomplete_component(component)
                        print(f"{path}: {message}", file=sys.stderr)
                    had_errors = True
                if c_result.complete or c_result.partial:
                    # A partial result still proves its findings for every other group.
                    tree = c_result.tree

            m_results: tuple[MacroAnalysisResult, ...] = ()
            if enable_macros:
                m_results = analyze_macros(
                    source,
                    filename=str(path),
                    symbolic_literals=symbolic_literals,
                )
                for r in m_results:
                    if not r.complete and r.incomplete:
                        print(f"{path}: {_format_incomplete(r.incomplete)}", file=sys.stderr)
                        had_errors = True

            file_results.append((path, tree, m_results, c_result))
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
            for path, tree, m_results, c_result in file_results:
                f_data, has_visible = _build_file_json(
                    path,
                    tree,
                    m_results,
                    verbose=args.verbose,
                    enable_macros=enable_macros,
                    symbolic_literals=symbolic_literals,
                    analysis=c_result,
                )
                if args.verbose or has_visible:
                    files.append(f_data)
            print(json.dumps({"files": files}, indent=2))
        elif file_results:
            f_data, _ = _build_file_json(
                file_results[0][0],
                file_results[0][1],
                file_results[0][2],
                verbose=args.verbose,
                enable_macros=enable_macros,
                symbolic_literals=symbolic_literals,
                analysis=file_results[0][3],
            )
            print(json.dumps(f_data, indent=2))
    elif batch_mode:
        color = sys.stdout.isatty()
        reports = []
        for path, tree, m_results, c_result in file_results:
            report, has_entries = _render_combined_report(
                tree,
                m_results,
                verbose=args.verbose,
                color=color,
                incomplete_components=c_result.incomplete_components if c_result else (),
            )
            if args.verbose or has_entries:
                reports.append(
                    "\n".join((_engine._colored(f"== {path} ==", "cyan", color), report))
                )
        if reports:
            print("\n\n".join(reports))
    elif file_results:
        c_result = file_results[0][3]
        report, _ = _render_combined_report(
            file_results[0][1],
            file_results[0][2],
            verbose=args.verbose,
            color=sys.stdout.isatty(),
            incomplete_components=c_result.incomplete_components if c_result else (),
        )
        print(report)

    if had_errors:
        return 2
    has_findings = False
    for _, tree, m_results, _ in file_results:
        if tree is not None and _engine._has_findings(tree):
            has_findings = True
        if any(r.simplified for r in m_results):
            has_findings = True
    return 1 if args.fail_on_findings and has_findings else 0
