"""Internal compatibility facade for the refactored analyzer implementation.

The implementation now lives in focused modules. This module keeps historical
internal names available for the existing test suite and transitional callers;
downstream consumers must use the documented top-level :mod:`cpre` API.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Historical tests and callers execute this file directly as a script. In that
# mode there is no package context, so ensure the repository/package parent is
# searched before this directory (which also contains ``cpre.py``) and use
# absolute imports. Normal package imports continue to use relative imports.
if __package__ in {None, ""}:
    package_parent = str(Path(__file__).resolve().parent.parent)
    while package_parent in sys.path:
        sys.path.remove(package_parent)
    sys.path.insert(0, package_parent)

    from cpre import reporting as _reporting
    from cpre.analysis import analyze_source, analyze_tree
    from cpre.analysis import tree_expressions as _tree_expressions
    from cpre.discovery import SOURCE_SUFFIXES as _SOURCE_SUFFIXES
    from cpre.discovery import source_paths as _source_paths
    from cpre.expressions import (
        ExpressionParser as _ExpressionParser,
    )
    from cpre.expressions import (
        conjunction,
        disjunction,
        expression_atoms,
        expression_predicates,
        format_expression,
        negate,
        parse_expression,
        simplify,
    )
    from cpre.expressions import (
        expression_atoms_in_order as _expression_atoms_in_order,
    )
    from cpre.expressions import (
        expression_comparison_key as _expression_comparison_key,
    )
    from cpre.expressions import (
        expressions_differ as _expressions_differ,
    )
    from cpre.expressions import (
        tokens as _tokens,
    )
    from cpre.model import (
        FALSE,
        TRUE,
        BooleanAtom,
        BranchAnalysis,
        ConditionalBranch,
        ConditionalGroup,
        ConditionalTree,
        ConditionError,
        Conjunction,
        Constant,
        DirectiveStructureError,
        Disjunction,
        Expression,
        ExpressionSyntaxError,
        Negation,
        Predicate,
        Variable,
    )
    from cpre.model import (
        SourceLocation as _SourceLocation,
    )
    from cpre.parser import (
        DIRECTIVE_RE as _DIRECTIVE_RE,
    )
    from cpre.parser import (
        LogicalLine as _LogicalLine,
    )
    from cpre.parser import (
        directive_expression as _directive_expression,
    )
    from cpre.parser import (
        logical_lines as _logical_lines,
    )
    from cpre.parser import (
        parse_source,
    )
    from cpre.parser import (
        remainder_location as _remainder_location,
    )
    from cpre.parser import (
        strip_comments as _strip_comments,
    )
    from cpre.robdd import BDD as _BDD
    from cpre.robdd import exact_simplify, simplify_under
else:
    from . import reporting as _reporting
    from .analysis import analyze_source, analyze_tree
    from .analysis import tree_expressions as _tree_expressions
    from .discovery import SOURCE_SUFFIXES as _SOURCE_SUFFIXES
    from .discovery import source_paths as _source_paths
    from .expressions import (
        ExpressionParser as _ExpressionParser,
    )
    from .expressions import (
        conjunction,
        disjunction,
        expression_atoms,
        expression_predicates,
        format_expression,
        negate,
        parse_expression,
        simplify,
    )
    from .expressions import (
        expression_atoms_in_order as _expression_atoms_in_order,
    )
    from .expressions import (
        expression_comparison_key as _expression_comparison_key,
    )
    from .expressions import (
        expressions_differ as _expressions_differ,
    )
    from .expressions import (
        tokens as _tokens,
    )
    from .model import (
        FALSE,
        TRUE,
        BooleanAtom,
        BranchAnalysis,
        ConditionalBranch,
        ConditionalGroup,
        ConditionalTree,
        ConditionError,
        Conjunction,
        Constant,
        DirectiveStructureError,
        Disjunction,
        Expression,
        ExpressionSyntaxError,
        Negation,
        Predicate,
        Variable,
    )
    from .model import (
        SourceLocation as _SourceLocation,
    )
    from .parser import (
        DIRECTIVE_RE as _DIRECTIVE_RE,
    )
    from .parser import (
        LogicalLine as _LogicalLine,
    )
    from .parser import (
        directive_expression as _directive_expression,
    )
    from .parser import (
        logical_lines as _logical_lines,
    )
    from .parser import (
        parse_source,
    )
    from .parser import (
        remainder_location as _remainder_location,
    )
    from .parser import (
        strip_comments as _strip_comments,
    )
    from .robdd import BDD as _BDD
    from .robdd import exact_simplify, simplify_under


_Visibility = _reporting.Visibility
_branch_differs_from_source = _reporting.branch_differs_from_source
_branch_is_notable = _reporting.branch_is_notable
_colored = _reporting.colored
_has_findings = _reporting.has_findings


def _with_legacy_notability(function, *args, **kwargs):
    """Run a reporting helper honoring monkeypatches on this legacy facade."""

    original = _reporting.branch_is_notable
    _reporting.branch_is_notable = _branch_is_notable
    try:
        return function(*args, **kwargs)
    finally:
        _reporting.branch_is_notable = original


def _compute_visibility(tree, verbose):
    return _with_legacy_notability(_reporting.compute_visibility, tree, verbose)


def _render_report(tree, *, verbose=True, color=False):
    return _with_legacy_notability(_reporting.render_report, tree, verbose=verbose, color=color)


def format_report(tree, *, verbose=True, color=False):
    return _with_legacy_notability(_reporting.format_report, tree, verbose=verbose, color=color)


def tree_to_dict(tree, *, verbose=True):
    return _with_legacy_notability(_reporting.tree_to_dict, tree, verbose=verbose)


def main(argv=None) -> int:
    """Compatibility entry point; CLI implementation lives in :mod:`cpre.cli`."""

    if __package__ in {None, ""}:
        from cpre.cli import main as cli_main
    else:
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
