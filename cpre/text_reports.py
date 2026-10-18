"""Human-readable text for diagnostics, macro reports and rewrite diffs.

These builders return strings (or lines) and write nothing, so any front end can
use them. Conditional-tree text lives in :mod:`cpre.reporting`; the JSON forms of the
same results live in :mod:`cpre.serialization`.
"""

from __future__ import annotations

import difflib
import shlex
from collections.abc import Sequence
from pathlib import Path

from . import reporting
from ._types import AnalysisIncomplete, AnalysisTelemetry, IncompleteComponent
from .errors import CpreError
from .macro_analysis import MacroAnalysisResult
from .model import ConditionalTree
from .preprocessing import PreprocessDiagnostic
from .unknown_macros import MacroUse, UnknownMacro


def format_error(error: CpreError) -> str:
    if error.location is None:
        return error.message
    if error.location.column is None:
        return f"line {error.location.line}: {error.message}"
    return f"{error.message} at line {error.location.line}, column {error.location.column}"


def format_incomplete(diagnostic: AnalysisIncomplete | PreprocessDiagnostic) -> str:
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


def format_incomplete_component(component: IncompleteComponent) -> str:
    """One stderr line per incomplete component of an otherwise analyzed source."""
    first = component.groups[0].location.line
    reasons = "; ".join(diagnostic.message for diagnostic in component.diagnostics)
    noun = "conditionals" if len(component.groups) > 1 else "conditional"
    return (
        f"line {first}: {noun} at lines {_group_span(component)} "
        f"not analyzed ({reasons}); findings cover only the rest of the file"
    )


def incomplete_lines(
    components: Sequence[IncompleteComponent], *, color: bool = False
) -> list[str]:
    if not components:
        return []
    lines = [
        reporting.colored(
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
                reporting.colored(
                    f"  {span}: #{group.directive}{condition} [incomplete: {reasons}]",
                    "yellow",
                    color,
                )
            )
    return lines


def unknown_macro_hint(item: UnknownMacro) -> str:
    if MacroUse.VALUE in item.uses:
        return f"supply -D {item.name}=<value>, or -U {item.name} to evaluate it as 0"
    return f"supply -D {item.name} or -U {item.name}"


INCLUDE_HINT = "pass -I DIR (or --iquote DIR) to resolve includes, or --skip-includes to mask them"


def include_hint(message: str) -> str | None:
    """Return a how-to-fix hint when ``message`` reports unresolved include processing."""
    if "include processing is not supported" in message:
        return INCLUDE_HINT
    return None


PRAGMA_HINT = (
    "pass --consume-pragma PREFIX to accept pragmas you know are harmless "
    "(for example --consume-pragma once)"
)


def pragma_hint(message: str) -> str | None:
    """Return a how-to-fix hint when ``message`` reports an unhandled reachable pragma."""
    if (
        "#pragma preprocessing directive is not supported" in message
        or "reachable pragma requires caller-provided pragma handling" in message
    ):
        return PRAGMA_HINT
    _, found, payload = message.partition("unsupported by the caller's pragma handler: ")
    if found and payload.split():
        first = payload.split()[0].split("(")[0]
        return f"add --consume-pragma {shlex.quote(first)} to accept this pragma"
    return None


def render_macro_report(
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
            if not r.rewritable:
                tag = "proven equivalent; not rewritten: parenthesize the original first"
            elif rewritten:
                tag = "rewritten, proven equivalent"
            else:
                tag = "proven equivalent"
            line = (
                f"{loc}#define {r.name} {r.original_replacement} -> "
                f"{r.simplified_replacement} [{tag}]"
            )
            lines.append(reporting.colored(line, "green", color))
        elif verbose:
            if r.candidate:
                reason = r.reason or "simplest equivalent form"
                line = f"{loc}#define {r.name} {r.original_replacement} ({reason})"
                lines.append(reporting.colored(line, "gray", color))
            else:
                line = f"{loc}#define {r.name} skipped: {r.reason}"
                lines.append(reporting.colored(line, "gray", color))
    if lines:
        return "\n".join(lines), has_entries
    if results:
        return "No simplifiable macro definitions found.", False
    return "No macro definitions found.", False


def render_combined_report(
    tree: ConditionalTree | None,
    macros: Sequence[MacroAnalysisResult],
    *,
    verbose: bool = True,
    color: bool = False,
    incomplete_components: Sequence[IncompleteComponent] = (),
) -> tuple[str, bool]:
    sections: list[str] = []
    has_any = False
    if tree is not None:
        c_report, c_has = reporting.render_report(tree, verbose=verbose, color=color)
        if c_has or (verbose and c_report):
            sections.append(c_report)
            if c_has:
                has_any = True
    if incomplete_components:
        sections.append("\n".join(incomplete_lines(incomplete_components, color=color)))
        has_any = True
    if macros:
        m_report, m_has = render_macro_report(macros, verbose=verbose, color=color)
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


def _diff_lines(text: str) -> list[str]:
    """Split on ``\\n`` only; ``str.splitlines`` also splits on form feeds and lone CRs."""
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    return lines if lines[-1] else lines[:-1]


def unified_diff(path: Path, before: str, after: str) -> str:
    """Deterministic unified diff of one file, applicable with ``patch -p0``."""
    lines: list[str] = []
    for line in difflib.unified_diff(
        _diff_lines(before),
        _diff_lines(after),
        fromfile=str(path),
        tofile=str(path),
    ):
        if line.endswith("\n"):
            lines.append(line)
        else:
            # Last line without a newline, marked the way diff and patch expect.
            lines.append(line + "\n\\ No newline at end of file\n")
    return "".join(lines)


def telemetry_lines(telemetry: AnalysisTelemetry) -> list[str]:
    """Diagnostic lines describing one analysis's resource use (not a stable format)."""
    limits = telemetry.limits
    lines = [
        f"atoms={telemetry.atoms} components={len(telemetry.components)} "
        f"work={telemetry.work}/{limits.max_work} "
        f"(limits: atoms<={limits.max_atoms} bdd_nodes<={limits.max_bdd_nodes})"
    ]
    for index, item in enumerate(telemetry.components):
        status = item.status if item.resource is None else f"{item.status} ({item.resource})"
        lines.append(
            f"component {index}: atoms={item.atoms} groups={item.groups} "
            f"nodes={item.nodes} {status}"
        )
    return lines
