"""Boolean simplification analysis for object-like #define replacement lists."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .api import AnalysisIncomplete, AnalysisOptions, SourceRange
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expressions import (
    ExpressionParser,
    expression_atoms_in_order,
    expressions_differ,
    format_expression,
    tokens,
)
from .macros import MacroDefinition
from .model import (
    TRUE,
    Expression,
    Predicate,
)
from .parser import logical_lines
from .robdd import (
    BDD,
    AnalysisBudget,
    AnalysisLimitExceeded,
    exact_simplify,
)
from .symbolic import ordered_atoms


@dataclass(frozen=True)
class MacroAnalysisResult:
    """Structured result of Boolean simplification analysis on a macro definition."""

    name: str
    definition: MacroDefinition
    candidate: bool
    reason: str | None = None
    original_replacement: str = ""
    original_expression: Expression | None = None
    simplified_expression: Expression | None = None
    simplified_replacement: str | None = None
    is_equivalent: bool | None = None
    incomplete: AnalysisIncomplete | None = None
    replacement_range: SourceRange | None = None

    @property
    def simplified(self) -> bool:
        """True if the candidate was simplified to a different equivalent expression."""
        return (
            self.simplified_replacement is not None
            and self.simplified_replacement != self.original_replacement
        )

    @property
    def complete(self) -> bool:
        """True if analysis completed without hitting resource limits."""
        return self.incomplete is None

    @property
    def location(self) -> SourceLocation | None:
        """Source location of the macro definition."""
        return self.definition.location

    @property
    def equivalent(self) -> bool | None:
        """Alias for is_equivalent."""
        return self.is_equivalent


def classify_macro_candidate(
    definition: MacroDefinition,
) -> tuple[bool, str | None, Expression | None, bool]:
    """Classify whether a MacroDefinition is a valid Boolean-expression candidate.

    Returns (is_candidate, reason, parsed_expression, is_wrapped).
    """
    if definition.parameters is not None:
        return False, "function-like macros are not supported", None, False

    text = definition.replacement.strip()
    if not text:
        return False, "empty replacement list", None, False

    try:
        toks = tokens(text)
    except Exception as error:
        return False, f"syntax error in replacement list: {error}", None, False

    if not toks:
        return False, "empty replacement list", None, False

    # Check for disallowed token kinds and inspect tokens conservatively
    for i, tok in enumerate(toks):
        if tok.kind == "string":
            return False, "string literals are not supported in Boolean expressions", None, False
        if tok.kind == "other":
            return False, f"unsupported operator or token: {tok.text!r}", None, False
        if tok.kind == "number":
            digits = re.sub(r"[uUlL]+$", "", tok.text)
            base = (
                16
                if digits.lower().startswith("0x")
                else (8 if len(digits) > 1 and digits.startswith("0") else 10)
            )
            try:
                val = int(digits, base)
            except ValueError:
                return False, f"invalid integer literal: {tok.text!r}", None, False
            if val not in (0, 1):
                return False, f"non-Boolean integer literal: {tok.text!r}", None, False
        elif tok.kind == "identifier":
            if tok.text == "defined":
                return False, "'defined' is not supported in macro replacement lists", None, False
            if i + 1 < len(toks) and toks[i + 1].kind == "lparen":
                return False, f"function calls are not supported: {tok.text}(...)", None, False

    # Check parentheses balance
    openings = 0
    for tok in toks:
        if tok.kind == "lparen":
            openings += 1
        elif tok.kind == "rparen":
            openings -= 1
            if openings < 0:
                return False, "unmatched ')' in replacement list", None, False
    if openings != 0:
        return False, "unmatched '(' in replacement list", None, False

    # Must contain at least one Boolean operator (&&, ||, !)
    has_bool_op = any(tok.kind in {"and", "or", "not"} for tok in toks)
    if not has_bool_op:
        return False, "replacement list contains no Boolean operators", None, False

    is_wrapped = ExpressionParser._is_wrapped(toks)

    try:
        parser = ExpressionParser(text)
        expr = parser.parse()
    except Exception as error:
        return False, f"malformed Boolean expression: {error}", None, False

    # Ensure no opaque Predicate was created (which would mean unmodeled C syntax)
    for atom in ordered_atoms(expr):
        if isinstance(atom, Predicate):
            return False, f"expression contains non-Boolean predicate: {atom.text!r}", None, False

    return True, None, expr, is_wrapped


def analyze_macro(
    definition: MacroDefinition,
    *,
    options: AnalysisOptions | None = None,
    replacement_range: SourceRange | None = None,
) -> MacroAnalysisResult:
    """Analyze one macro definition for Boolean simplification."""
    resolved_options = options or AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance",
            code=ErrorCode.ANALYSIS_FAILURE,
        )

    is_candidate, reason, expr, is_wrapped = classify_macro_candidate(definition)
    if not is_candidate or expr is None:
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=False,
            reason=reason,
            original_replacement=definition.replacement,
            replacement_range=replacement_range,
        )

    # Candidate expression: apply bounded ROBDD simplification
    atoms = tuple(dict.fromkeys(expression_atoms_in_order(expr)))
    limits = resolved_options._resource_limits()
    budget = AnalysisBudget(limits.max_work)

    try:
        if len(atoms) > limits.max_atoms:
            raise AnalysisLimitExceeded("atoms", limits.max_atoms, len(atoms))
        bdd = BDD(atoms, limits=limits, budget=budget)
        simplified_expr = exact_simplify(expr, bdd)
        is_eq = bdd.equivalent_under(TRUE, expr, simplified_expr)
    except AnalysisLimitExceeded as error:
        diagnostic = AnalysisIncomplete(
            code=ErrorCode.ANALYSIS_LIMIT_EXCEEDED,
            resource=error.resource,
            limit=error.limit,
            observed=error.observed,
            message=str(error),
            location=definition.location,
        )
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=True,
            reason=f"resource limit exceeded: {error}",
            original_replacement=definition.replacement,
            original_expression=expr,
            simplified_expression=None,
            simplified_replacement=None,
            is_equivalent=None,
            incomplete=diagnostic,
            replacement_range=replacement_range,
        )

    differ = expressions_differ(expr, simplified_expr)
    if differ:
        formatted = format_expression(simplified_expr)
        simplified_replacement = f"({formatted})" if is_wrapped else formatted
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=True,
            reason=None,
            original_replacement=definition.replacement,
            original_expression=expr,
            simplified_expression=simplified_expr,
            simplified_replacement=simplified_replacement,
            is_equivalent=is_eq,
            incomplete=None,
            replacement_range=replacement_range,
        )
    else:
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=True,
            reason="expression is already in simplest equivalent form",
            original_replacement=definition.replacement,
            original_expression=expr,
            simplified_expression=expr,
            simplified_replacement=None,
            is_equivalent=is_eq,
            incomplete=None,
            replacement_range=replacement_range,
        )


def _to_source_location(loc: object) -> SourceLocation:
    line = getattr(loc, "line", 1) or 1
    column = getattr(loc, "column", None)
    return SourceLocation(line=line, column=column)


def analyze_macros(
    source: str,
    *,
    options: AnalysisOptions | None = None,
    filename: str | None = None,
) -> tuple[MacroAnalysisResult, ...]:
    """Analyze all macro definitions in a source string for Boolean simplification."""
    resolved_options = options or AnalysisOptions()
    results: list[MacroAnalysisResult] = []

    for line in logical_lines(source):
        match = re.match(r"^\s*#\s*define\b(.*)$", line.text)
        if match is None:
            continue
        remainder = match.group(1)
        rem_offset = match.start(1)
        leading_ws = len(remainder) - len(remainder.lstrip())
        text = remainder.lstrip()
        name_match = re.match(r"([A-Za-z_]\w*)", text)
        if not name_match:
            continue
        name = name_match.group(1)
        name_offset = rem_offset + leading_ws
        name_location = (
            _to_source_location(line.locations[name_offset])
            if name_offset < len(line.locations)
            else SourceLocation(line.start_line, 1)
        )
        tail = text[name_match.end() :]
        parameters: tuple[str, ...] | None = None
        variadic = False
        if tail.startswith("("):
            # Function-like macro
            end = tail.find(")")
            if end != -1:
                parts = [p.strip() for p in tail[1:end].split(",")] if tail[1:end].strip() else []
                if parts and parts[-1] == "...":
                    variadic = True
                    parts.pop()
                parameters = tuple(parts)
                tail_rep = tail[end + 1 :]
            else:
                parameters = ()
                tail_rep = tail
            definition = MacroDefinition(
                name=name,
                replacement=tail_rep.strip(),
                parameters=parameters,
                variadic=variadic,
                location=name_location,
            )
            result = analyze_macro(definition, options=resolved_options)
            results.append(result)
            continue

        # Object-like macro
        if tail and not tail[0].isspace():
            definition = MacroDefinition(
                name=name,
                replacement=tail.strip(),
                location=name_location,
            )
            results.append(
                MacroAnalysisResult(
                    name=name,
                    definition=definition,
                    candidate=False,
                    reason="object-like macro replacement requires whitespace after name",
                    original_replacement=tail.strip(),
                )
            )
            continue

        replacement = tail.strip()
        replacement_range = None
        if replacement:
            rep_start_in_tail = len(tail) - len(tail.lstrip())
            first_char_offset = rem_offset + leading_ws + name_match.end() + rep_start_in_tail
            last_char_offset = first_char_offset + len(replacement) - 1
            if last_char_offset < len(line.locations):
                start_loc = _to_source_location(line.locations[first_char_offset])
                last_loc = _to_source_location(line.locations[last_char_offset])
                replacement_range = SourceRange(
                    start=start_loc,
                    end=SourceLocation(last_loc.line, (last_loc.column or 1) + 1),
                )

        definition = MacroDefinition(
            name=name,
            replacement=replacement,
            parameters=None,
            location=name_location,
        )
        result = analyze_macro(
            definition,
            options=resolved_options,
            replacement_range=replacement_range,
        )
        results.append(result)

    return tuple(results)


__all__ = [
    "MacroAnalysisResult",
    "analyze_macro",
    "analyze_macros",
    "classify_macro_candidate",
]
