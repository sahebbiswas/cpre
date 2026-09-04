"""Internal compatibility facade for the refactored analyzer implementation.

The implementation now lives in focused modules. This module keeps historical
internal names available for the existing test suite and transitional callers;
downstream consumers must use the documented top-level :mod:`cpre` API.
"""

from __future__ import annotations

from .analysis import analyze_source, analyze_tree, tree_expressions as _tree_expressions
from .discovery import SOURCE_SUFFIXES as _SOURCE_SUFFIXES, source_paths as _source_paths
from .expressions import (
    ExpressionParser as _ExpressionParser,
    conjunction,
    disjunction,
    expression_atoms,
    expression_atoms_in_order as _expression_atoms_in_order,
    expression_comparison_key as _expression_comparison_key,
    expression_predicates,
    expressions_differ as _expressions_differ,
    format_expression,
    negate,
    parse_expression,
    simplify,
    tokens as _tokens,
)
from .model import (
    BooleanAtom,
    BranchAnalysis,
    ConditionError,
    ConditionalBranch,
    ConditionalGroup,
    ConditionalTree,
    Conjunction,
    Constant,
    DirectiveStructureError,
    Disjunction,
    Expression,
    ExpressionSyntaxError,
    FALSE,
    Negation,
    Predicate,
    SourceLocation as _SourceLocation,
    TRUE,
    Variable,
)
from .parser import (
    DIRECTIVE_RE as _DIRECTIVE_RE,
    LogicalLine as _LogicalLine,
    directive_expression as _directive_expression,
    logical_lines as _logical_lines,
    parse_source,
    remainder_location as _remainder_location,
    strip_comments as _strip_comments,
)
from .reporting import (
    Visibility as _Visibility,
    branch_differs_from_source as _branch_differs_from_source,
    branch_is_notable as _branch_is_notable,
    colored as _colored,
    compute_visibility as _compute_visibility,
    format_report,
    has_findings as _has_findings,
    render_report as _render_report,
    tree_to_dict,
)
from .robdd import BDD as _BDD, exact_simplify, simplify_under


def main(argv=None) -> int:
    """Compatibility entry point; CLI implementation lives in :mod:`cpre.cli`."""

    from .cli import main as cli_main

    return cli_main(argv)


__all__ = [
    "BooleanAtom",
    "BranchAnalysis",
    "ConditionError",
    "ConditionalBranch",
    "ConditionalGroup",
    "ConditionalTree",
    "Conjunction",
    "Constant",
    "DirectiveStructureError",
    "Disjunction",
    "Expression",
    "ExpressionSyntaxError",
    "FALSE",
    "Negation",
    "Predicate",
    "TRUE",
    "Variable",
    "analyze_source",
    "analyze_tree",
    "conjunction",
    "disjunction",
    "exact_simplify",
    "expression_atoms",
    "expression_predicates",
    "format_expression",
    "format_report",
    "main",
    "negate",
    "parse_expression",
    "parse_source",
    "simplify",
    "simplify_under",
    "tree_to_dict",
]


if __name__ == "__main__":
    raise SystemExit(main())
