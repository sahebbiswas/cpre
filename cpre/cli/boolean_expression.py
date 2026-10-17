"""``cpre test-input``: simplify an arbitrary Boolean expression with an ROBDD."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .._types import AnalysisOptions
from ..errors import AnalysisError, CpreError
from ..expressions import (
    expression_atoms_in_order,
    expressions_differ,
    format_expression,
    parse_expression,
)
from ..macro_analysis import _symbolize
from ..model import TRUE, ConditionError, Predicate
from ..robdd import BDD, AnalysisBudget, AnalysisLimitExceeded, exact_simplify
from ..serialization import expression_document
from ._common import EXIT_ERROR, EXIT_OK, add_symbolic_literal_options, symbolic_literals_from


def build_parser(prog: str) -> argparse.ArgumentParser:
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
    add_symbolic_literal_options(parser, "expression")
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


def _analysis_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> AnalysisOptions:
    options_kwargs: dict[str, int] = {}
    if args.max_atoms is not None:
        options_kwargs["max_atoms"] = args.max_atoms
    if args.max_bdd_nodes is not None:
        options_kwargs["max_bdd_nodes"] = args.max_bdd_nodes
    if args.max_work is not None:
        options_kwargs["max_work"] = args.max_work
    try:
        return AnalysisOptions(**options_kwargs)
    except (AnalysisError, ValueError) as error:
        parser.error(getattr(error, "message", str(error)))


def test_input_main(
    argv: Sequence[str] | None = None,
    prog: str = "cpre test-input",
) -> int:
    """Parse, simplify, and print an arbitrary Boolean expression."""
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    symbolic_literals = symbolic_literals_from(parser, args)
    options = _analysis_options(parser, args)

    try:
        parsed = parse_expression(args.expression)
    except (ConditionError, CpreError, RecursionError) as error:
        print(f"{prog}: error: {error}", file=sys.stderr)
        return EXIT_ERROR

    predicates = [atom for atom in expression_atoms_in_order(parsed) if isinstance(atom, Predicate)]
    if predicates:
        print(f"{prog}: error: unsupported syntax: {predicates[0].text!r}", file=sys.stderr)
        return EXIT_ERROR

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
        return EXIT_ERROR

    if not is_equivalent:
        print(
            f"{prog}: error: simplified expression could not be proven equivalent",
            file=sys.stderr,
        )
        return EXIT_ERROR

    simplified = format_expression(simplified_expr)
    if args.json:
        document = expression_document(
            args.expression,
            simplified,
            equivalent=is_equivalent,
            changed=expressions_differ(expr, simplified_expr),
            symbolic_literals=symbolic_literals,
        )
        print(json.dumps(document, indent=2))
    else:
        print(f"Input:      {args.expression}")
        print(f"Simplified: {simplified}")
    return EXIT_OK
