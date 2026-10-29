"""Human-readable text for diagnostics, macro reports and rewrite diffs.

These builders return strings (or lines) and write nothing, so any front end can
use them. Conditional-tree text lives in :mod:`cpre.reporting`; the JSON forms of the
same results live in :mod:`cpre.serialization`.
"""

from __future__ import annotations

import difflib
import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from . import reporting
from ._evaluation_types import TRACE_LIMIT
from ._style import PLAIN, Style, style_for
from ._types import AnalysisIncomplete, AnalysisTelemetry, IncompleteComponent
from .errors import CpreError
from .evaluation import Evaluation, EvaluationState, Trace, TraceKind
from .macro_analysis import MacroAnalysisResult
from .model import ConditionalTree
from .preprocessing import PreprocessDiagnostic
from .reporting import FindingCounts, label_width, line_label
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
    components: Sequence[IncompleteComponent],
    *,
    color: bool = False,
    style: Style | None = None,
) -> list[str]:
    if not components:
        return []
    style = style_for(style, color)
    spans = [
        (
            f"L{group.location.line}-{group.end_line}"
            if group.end_line is not None
            else f"L{group.location.line}",
            group,
            "; ".join(diagnostic.message for diagnostic in component.diagnostics),
        )
        for component in components
        for group in component.groups
    ]
    width = max(len(span) for span, _, _ in spans)
    lines = [
        style.paint(
            "Incomplete analysis (no findings are reported for these conditionals):",
            "incomplete",
        )
    ]
    for span, group, reasons in spans:
        condition = f" {group.condition}" if group.condition else ""
        lines.append(
            f"{span.ljust(width)} {reporting.badge(style, 'INCOMPLETE', 'incomplete')} "
            f"{style.paint('#' + group.directive, 'bold')}{condition}"
        )
        lines.append(f"{' ' * width}   {style.paint('why', 'dim')}  {reasons}")
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


def _macro_header(
    style: Style, label: str, role: str, width: int, result: MacroAnalysisResult
) -> str:
    line = result.location.line if result.location and result.location.line else None
    where = line_label(line, width) if line else " " * width
    return f"{where} {reporting.badge(style, label, role)} {style.paint('#define', 'bold')} {result.name}"


def render_macro_report(
    results: Sequence[MacroAnalysisResult],
    *,
    verbose: bool = True,
    color: bool = False,
    rewritten: bool = False,
    style: Style | None = None,
) -> tuple[str, bool]:
    style = style_for(style, color)
    shown = [r for r in results if r.simplified or verbose]
    width = label_width(
        r.location.line for r in shown if r.location is not None and r.location.line is not None
    )
    lines: list[str] = []
    has_entries = False
    for r in shown:
        if r.simplified:
            has_entries = True
            label = "REWRITTEN" if r.rewritable and rewritten else "SIMPLIFY"
            lines.append(_macro_header(style, label, "simplify", width, r))
            note = "rewritten, proven equivalent" if label == "REWRITTEN" else "proven equivalent"
            lines.extend(
                reporting.transformation_lines(
                    style,
                    "   ",
                    r.original_replacement,
                    [(r.simplified_replacement or "", note)],
                )
            )
            if r.expanded:
                lines.append(
                    f"   {style.paint('hint', 'dim')}  "
                    f"expanded {', '.join(r.expanded_from)} to {r.expanded_replacement}; "
                    "reported only, never rewritten"
                )
            elif not r.rewritable:
                lines.append(
                    f"   {style.paint('hint', 'dim')}  "
                    "not rewritten: parenthesize the original first"
                )
        elif r.candidate:
            reason = r.reason or "simplest equivalent form"
            header = _macro_header(style, "ok", "ok", width, r)
            lines.append(f"{header} {r.original_replacement}  {style.paint(f'({reason})', 'dim')}")
        else:
            header = _macro_header(style, "skip", "skip", width, r)
            lines.append(f"{header}  {style.paint(str(r.reason), 'dim')}")
    if lines:
        return "\n".join(lines), has_entries
    if results:
        return "No simplifiable macro definitions found.", False
    return "No macro definitions found.", False


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _visible(text: str) -> str:
    """``text`` with each control character replaced by a ``\\xNN`` escape."""
    return _CONTROL.sub(lambda match: f"\\x{ord(match.group()):02x}", text)


def render_evaluation(evaluation: Evaluation, style: Style | None = None) -> str:
    """One evaluation as a ``-`` expression line and a ``+`` value line, or why there is none.

    A concrete result shows the value (marked unsigned when it is a ``uintmax_t``) and,
    when macro expansion changed the text, the expanded expression. A symbolic one shows
    that expression on a ``~`` line, with every settled macro substituted and the names
    that are not settled listed under it. An unevaluable one shows a caret under the
    token it concerns, the reason and the message. The layout is the same with and
    without color.
    """
    style = style_for(style, False)
    lines: list[str] = []
    expanded = evaluation.expanded
    note = None if expanded is None or expanded == evaluation.expression else expanded
    # Operands and replacement text come from the caller or a seed file, so control
    # characters are shown as escapes rather than sent to the terminal. JSON is untouched.
    operand = _visible(evaluation.expression)
    message = _visible(evaluation.message or "")
    if note is not None:
        note = _visible(note)
    if evaluation.symbolic:
        lines.append(f"{style.paint('-', 'before')} {style.paint(operand, 'before')}")
        shown = _visible(expanded if expanded is not None else evaluation.expression)
        lines.append(f"{style.paint('~', 'opaque')} {style.paint(shown, 'opaque')}")
        names = ", ".join(evaluation.unresolved)
        lines.append(f"  {style.paint('unresolved', 'dim')}  {names}")
    elif evaluation.concrete:
        suffix = "unsigned" if evaluation.unsigned else None
        lines.extend(
            reporting.transformation_lines(style, "", operand, [(str(evaluation.value), suffix)])
        )
    else:
        lines.append(f"{style.paint('-', 'before')} {style.paint(operand, 'before')}")
        offset = evaluation.offset
        # The caret is only meaningful when the operand is shown exactly as given; for a
        # macro it sits under the macro's name, the place its replacement is located at.
        if offset is not None and operand == evaluation.expression and offset <= len(operand):
            lines.append(f"  {' ' * offset}{style.paint('^', 'incomplete')}")
        reason = evaluation.reason.value if evaluation.reason is not None else "unevaluable"
        lines.append(
            f"{style.paint('!', 'incomplete')} {style.paint(reason, 'incomplete')}: {message}"
        )
    if note is not None and not evaluation.symbolic:
        lines.append(f"  {style.paint('expanded', 'dim')}  {note}")
    if evaluation.trace is not None:
        lines.extend(trace_lines(evaluation.trace, style))
    return "\n".join(lines)


_TRACE_ROLES = {
    TraceKind.WARNING: "incomplete",
    TraceKind.CONVERSION: "incomplete",
    TraceKind.STOP: "incomplete",
    TraceKind.RESULT: "after",
}
_TRACE_STATE_ROLES = {
    EvaluationState.SYMBOLIC: "opaque",
    EvaluationState.UNEVALUABLE: "incomplete",
}


def trace_lines(trace: Trace, style: Style) -> list[str]:
    """The derivation of an evaluation, one line per step under a ``trace`` heading.

    The step kinds are the same words with and without color; color only marks the
    steps that deserve a second look (warnings, conversions, where evaluation stopped)
    and the result.
    """
    lines = [f"  {style.paint('trace', 'dim')}"]
    width = max((len(step.kind.value) for step in trace.steps), default=0)
    for step in trace.steps:
        text = _visible(step.text)
        replacement = _visible(step.replacement or "")
        note = None if step.note is None else _visible(step.note)
        label = style.paint(step.kind.value.ljust(width), _TRACE_ROLES.get(step.kind, "dim"))
        if step.kind is TraceKind.EXPAND:
            detail = f"{text} {style.arrow} {replacement}"
        elif step.kind in {TraceKind.EVALUATE, TraceKind.RESULT}:
            role = (
                "after"
                if step.kind is TraceKind.RESULT
                else _TRACE_STATE_ROLES.get(step.state or EvaluationState.CONCRETE)
            )
            outcome = style.paint(_step_value(step.state, step.value, step.unsigned), role or "dim")
            detail = f"{text} = {outcome}"
        elif step.kind is TraceKind.STOP:
            detail = note or text
        elif note is not None:
            detail = f"{text}: {note}" if step.kind is TraceKind.WARNING else f"{text}  ({note})"
        else:
            detail = text
        lines.append(f"    {label}  {detail}")
    if trace.truncated:
        lines.append(
            f"    {style.paint('truncated', 'incomplete')}  "
            f"steps beyond the first {TRACE_LIMIT} were dropped; the line above is the outcome"
        )
    return lines


def _step_value(state: EvaluationState | None, value: int | None, unsigned: bool | None) -> str:
    if state is EvaluationState.SYMBOLIC:
        return "symbolic"
    if state is EvaluationState.UNEVALUABLE or value is None:
        return "unevaluable"
    return f"{value} (unsigned)" if unsigned else str(value)


def count_macro_findings(results: Sequence[MacroAnalysisResult]) -> FindingCounts:
    """The simplifiable macro definitions among ``results``."""
    return FindingCounts(macros=sum(1 for r in results if r.simplified))


def file_findings(
    tree: ConditionalTree | None, macros: Sequence[MacroAnalysisResult]
) -> FindingCounts:
    """Every dead, redundant and simplifiable entry one source produced."""
    counts = count_macro_findings(macros)
    if tree is not None:
        counts += reporting.count_findings(tree)
    return counts


def summary_line(counts: FindingCounts, style: Style | None = None) -> str:
    """One line totalling findings by kind, or ``""`` when there are none."""
    style = style or PLAIN

    def plural(count: int, noun: str) -> str:
        return f"{count:,} {noun}{'' if count == 1 else 's'}"

    parts = []
    if counts.dead:
        parts.append(style.paint(f"{counts.dead:,} dead", "dead"))
    if counts.redundant:
        parts.append(style.paint(f"{counts.redundant:,} redundant", "redundant"))
    if counts.simplifiable:
        parts.append(style.paint(plural(counts.simplifiable, "simplifiable condition"), "simplify"))
    if counts.macros:
        parts.append(style.paint(plural(counts.macros, "simplifiable macro"), "simplify"))
    if not parts:
        return ""
    return f"{style.paint('Summary', 'bold')}  {style.paint(style.separator, 'dim').join(parts)}"


def render_combined_report(
    tree: ConditionalTree | None,
    macros: Sequence[MacroAnalysisResult],
    *,
    verbose: bool = True,
    color: bool = False,
    incomplete_components: Sequence[IncompleteComponent] = (),
    style: Style | None = None,
) -> tuple[str, bool]:
    style = style_for(style, color)
    sections: list[tuple[str | None, str]] = []
    has_any = False
    if tree is not None:
        c_report, c_has = reporting.render_report(tree, verbose=verbose, style=style)
        if c_has or (verbose and c_report):
            sections.append(("Conditionals", c_report))
            if c_has:
                has_any = True
    if incomplete_components:
        incomplete = "\n".join(incomplete_lines(incomplete_components, style=style))
        sections.append((None, incomplete))
        has_any = True
    if macros:
        m_report, m_has = render_macro_report(macros, verbose=verbose, style=style)
        if m_has or (verbose and m_report):
            sections.append(("Macros", m_report))
            if m_has:
                has_any = True
    if len(sections) == 1:
        return sections[0][1], has_any
    if sections:
        blocks = [
            text if title is None else f"{style.paint(title, 'heading')}\n{text}"
            for title, text in sections
        ]
        return "\n\n".join(blocks), has_any
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
