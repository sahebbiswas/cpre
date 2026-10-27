"""``cpre evaluate``: the integer value of a macro or expression under a configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence

from .._style import COLOR_CHOICES
from .._types import AnalysisOptions
from ..configuration import MacroConfiguration
from ..errors import CpreError
from ..evaluation import Evaluation, EvaluationState, evaluate_expression, evaluate_macro
from ..preprocessing import PreprocessingContext
from ..serialization import evaluations_document
from ..text_reports import format_error, render_evaluation
from ._common import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, EXIT_SYMBOLIC, output_style
from .preprocess_configuration import (
    add_configuration_options,
    configuration_from_arguments,
    require_seed_for_seed_options,
)

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


def build_parser(prog: str) -> argparse.ArgumentParser:
    """Build the argument parser for ``cpre evaluate``."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Evaluate macros and integer expressions to their values under one concrete "
            "macro configuration."
        ),
    )
    parser.add_argument(
        "items",
        nargs="+",
        metavar="NAME_OR_EXPRESSION",
        help=(
            "a macro name, evaluated as the macro's value, or an integer expression that may "
            "use macros; an expression that starts with '-' needs '--' before it"
        ),
    )
    add_configuration_options(parser)
    parser.add_argument(
        "--max-work",
        type=int,
        metavar="N",
        help="resource limit: work units for each item (default: 500000)",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "show how each value was derived: the macro expansions, warnings about use-site "
            "precedence and signed-to-unsigned conversions, and each evaluated operation"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write a JSON document with one evaluation record per item",
    )
    parser.add_argument(
        "--color",
        choices=COLOR_CHOICES,
        default="auto",
        metavar="WHEN",
        help="color the text report: auto (the default), always, or never",
    )
    parser.epilog = (
        "example: cpre evaluate -D WIDTH=3 -D HEIGHT=4 -D 'AREA=(WIDTH * HEIGHT)' AREA "
        "'AREA % 5' (exit status 1 when an item is unevaluable, 3 when one is only symbolic)"
    )
    return parser


def evaluate_item(
    item: str,
    configuration: MacroConfiguration,
    context: PreprocessingContext | None,
    options: AnalysisOptions,
    *,
    trace: bool = False,
) -> Evaluation:
    """A bare identifier is evaluated as a macro, anything else as an expression."""
    name = item.strip()
    if _IDENTIFIER.match(name):
        return evaluate_macro(name, configuration, context=context, options=options, trace=trace)
    return evaluate_expression(item, configuration, context=context, options=options, trace=trace)


def evaluate_main(argv: Sequence[str] | None = None, prog: str = "cpre evaluate") -> int:
    """Evaluate each operand: status 1 when one is unevaluable, 3 when some are only symbolic,
    2 when the run failed."""
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    require_seed_for_seed_options(parser, args)
    try:
        options = AnalysisOptions(**({} if args.max_work is None else {"max_work": args.max_work}))
    except CpreError as error:
        parser.error(error.message)
    try:
        built = configuration_from_arguments(args)
        if built is None:
            return EXIT_ERROR
        configuration, context = built
        evaluations = [
            evaluate_item(item, configuration, context, options, trace=args.explain)
            for item in args.items
        ]
    except (CpreError, ValueError) as error:
        message = format_error(error) if isinstance(error, CpreError) else str(error)
        print(f"{prog}: error: {message}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(evaluations_document(evaluations), indent=2, ensure_ascii=False))
    else:
        style = output_style(args)
        print("\n".join(render_evaluation(evaluation, style) for evaluation in evaluations))
    if any(evaluation.state is EvaluationState.UNEVALUABLE for evaluation in evaluations):
        return EXIT_FINDINGS
    if any(evaluation.symbolic for evaluation in evaluations):
        return EXIT_SYMBOLIC
    return EXIT_OK
