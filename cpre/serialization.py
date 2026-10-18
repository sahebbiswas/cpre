"""JSON-ready dictionaries for analysis and preprocessing results.

Every function returns plain ``dict``/``list``/``str``/``int``/``bool``/``None`` data
that ``json.dumps`` accepts, and none of them print or exit. The command line uses them
to build its ``--json`` output; other front ends can use them for the same documents.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from . import reporting
from ._types import AnalysisIncomplete, AnalysisResult, IncompleteComponent
from .macro_analysis import MacroAnalysisResult, MacroSimplificationResult
from .model import ConditionalTree
from .preprocessing import PreprocessDiagnostic, PreprocessResult
from .unknown_macros import UnknownMacro

SCHEMA_VERSION = 1
"""Major version of the JSON contract in ``docs/json-contract.md``.

Adding a field, a diagnostic ``code`` or a document kind keeps the version; renaming,
removing, retyping or re-meaning a field raises it.
"""


def schema_id(kind: str) -> str:
    """The ``schema`` value of a top-level document, such as ``cpre.analysis/1``."""
    return f"cpre.{kind}/{SCHEMA_VERSION}"


def top_level(kind: str, record: dict[str, object]) -> dict[str, object]:
    """``record`` as a top-level document of ``kind``, with ``schema`` as its first key."""
    return {"schema": schema_id(kind), **record}


def batch_document(kind: str, files: Sequence[dict[str, object]]) -> dict[str, object]:
    """The top-level document for a batch run, wrapping one record per file."""
    return top_level(kind, {"files": list(files)})


def semantics_name(symbolic_literals: tuple[int, ...]) -> str:
    return "symbolic-literal" if symbolic_literals else "ordinary"


def incomplete_to_dict(diagnostic: AnalysisIncomplete) -> dict[str, object]:
    return {
        "code": diagnostic.code.value,
        "resource": diagnostic.resource,
        "limit": diagnostic.limit,
        "observed": diagnostic.observed,
        "message": diagnostic.message,
        "line": diagnostic.location.line if diagnostic.location else None,
        "component": diagnostic.component,
    }


def unknown_macro_to_dict(item: UnknownMacro) -> dict[str, object]:
    return {
        "name": item.name,
        "uses": [use.value for use in item.uses],
        "lines": [location.line for location in item.locations],
        "suggestions": list(item.suggestions),
    }


def preprocess_diagnostic_to_dict(
    diagnostic: PreprocessDiagnostic | AnalysisIncomplete,
) -> dict[str, object]:
    if isinstance(diagnostic, AnalysisIncomplete):
        data = incomplete_to_dict(diagnostic)
        data["source_identity"] = diagnostic.source_identity
        return data
    return {
        "code": diagnostic.code.value,
        "message": diagnostic.message,
        "line": diagnostic.location.line,
        "source_identity": diagnostic.source_identity,
        "condition": diagnostic.condition,
        "unresolved": [unknown_macro_to_dict(item) for item in diagnostic.unresolved],
    }


def incomplete_component_to_dict(component: IncompleteComponent) -> dict[str, object]:
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
        "diagnostics": [incomplete_to_dict(d) for d in component.diagnostics],
    }


def analysis_status_dict(analysis: AnalysisResult) -> dict[str, object]:
    return {
        "complete": analysis.complete,
        "partial": analysis.partial,
        "incomplete": [incomplete_to_dict(d) for d in analysis.incomplete],
        "incomplete_components": [
            incomplete_component_to_dict(c) for c in analysis.incomplete_components
        ],
    }


def macro_to_dict(result: MacroAnalysisResult) -> dict[str, object]:
    return {
        "name": result.name,
        "line": result.location.line if result.location else None,
        "column": result.location.column if result.location else None,
        "candidate": result.candidate,
        "simplified": result.simplified,
        "original": result.original_replacement,
        "replacement": result.simplified_replacement,
        "equivalent": result.is_equivalent,
        "rewritable": result.rewritable,
        "reason": result.reason,
        "semantics": result.semantics,
        "symbolic_literals": list(result.symbolic_literals),
    }


def build_file_json(
    path: Path | str,
    tree: ConditionalTree | None,
    m_results: Sequence[MacroAnalysisResult],
    *,
    verbose: bool,
    enable_macros: bool,
    symbolic_literals: tuple[int, ...],
    analysis: AnalysisResult | None = None,
) -> tuple[dict[str, object], bool]:
    """The JSON document for one analyzed file and whether it has anything to report."""
    f_data: dict[str, object] = {"path": str(path)}
    has_visible = False
    if tree is not None:
        t_dict = reporting.tree_to_dict(tree, verbose=verbose)
        f_data["groups"] = t_dict["groups"]
        if t_dict["groups"]:
            has_visible = True
    else:
        f_data["groups"] = []
    if analysis is not None:
        f_data.update(analysis_status_dict(analysis))
        if not analysis.complete:
            has_visible = True
    else:
        # Conditional analysis did not run: the status is unknown, not "complete".
        f_data.update(complete=None, partial=None, incomplete=None, incomplete_components=None)

    if enable_macros:
        visible_macros = [macro_to_dict(r) for r in m_results if (verbose or r.simplified)]
        if any(r.simplified for r in m_results):
            has_visible = True
        f_data["macros"] = visible_macros
    else:
        f_data["macros"] = []

    f_data["semantics"] = semantics_name(symbolic_literals)
    f_data["symbolic_literals"] = list(symbolic_literals)
    return f_data, has_visible


def build_simplify_macros_json(
    path: Path | str,
    res: MacroSimplificationResult,
    *,
    verbose: bool,
    symbolic_literals: tuple[int, ...],
) -> tuple[dict[str, object], bool]:
    """The JSON document for one simplified file and whether it has any findings."""
    visible_macros = [macro_to_dict(r) for r in res.results if (verbose or r.simplified)]
    f_data: dict[str, object] = {
        "path": str(path),
        "rewritten": res.rewritten,
        "applied_count": res.applied_count,
        "verified": res.verified,
        "semantics": semantics_name(symbolic_literals),
        "symbolic_literals": list(symbolic_literals),
        "macros": visible_macros,
    }
    has_visible = res.has_findings
    return f_data, has_visible


def unknown_macros_document(path: Path | str, unknown: Sequence[UnknownMacro]) -> dict[str, object]:
    """The ``--list-unknown-macros --json`` document."""
    return top_level(
        "unknown-macros",
        {"file": str(path), "unknown_macros": [unknown_macro_to_dict(m) for m in unknown]},
    )


def preprocess_document(
    path: Path | str, result: PreprocessResult, source: str | None
) -> dict[str, object]:
    """The ``preprocess --json`` document; ``source`` is the output text to embed."""
    return top_level(
        "preprocess",
        {
            "file": str(path),
            "complete": result.complete,
            "source": source,
            "diagnostics": [preprocess_diagnostic_to_dict(d) for d in result.incomplete],
            "skipped_includes": [
                {
                    "directive": skipped.directive,
                    "operand": skipped.operand,
                    "line": skipped.location.line,
                    "source_identity": skipped.source_identity,
                }
                for skipped in result.skipped_includes
            ],
        },
    )


def expression_document(
    expression: str,
    simplified: str,
    *,
    equivalent: bool,
    changed: bool,
    symbolic_literals: tuple[int, ...],
) -> dict[str, object]:
    """The ``test-input --json`` document."""
    return top_level(
        "expression",
        {
            "input": expression,
            "simplified": simplified,
            "equivalent": equivalent,
            "changed": changed,
            "semantics": semantics_name(symbolic_literals),
            "symbolic_literals": list(symbolic_literals),
        },
    )
