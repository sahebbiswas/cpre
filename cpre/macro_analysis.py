"""Boolean simplification analysis for object-like #define replacement lists."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from ._lines import line_starts as _line_starts
from ._macro_inlining import (
    ConditionalTracker,
    DefineSite,
    Expander,
    Inliner,
    MacroScope,
    undefined_name,
)
from ._precedence import top_level_binary_operators
from ._recomposition import Expansion, choose_replacement, keeps_value
from ._types import (
    AnalysisIncomplete,
    AnalysisOptions,
    FixConfidence,
    SourceRange,
    SuggestedEdit,
)
from .errors import AnalysisError, ErrorCode, SourceLocation
from .expressions import (
    ExpressionParser,
    expression_atoms_in_order,
    expressions_differ,
    format_expression,
    tokens,
)
from .lexer import logical_lines
from .macros import MacroDefinition
from .model import (
    TRUE,
    Conjunction,
    Constant,
    Disjunction,
    Expression,
    Negation,
    Predicate,
    SymbolicLiteral,
    Variable,
)
from .robdd import (
    BDD,
    AnalysisBudget,
    AnalysisLimitExceeded,
    exact_simplify,
)
from .symbolic import ordered_atoms

ORDINARY_SEMANTICS = "ordinary"
SYMBOLIC_LITERAL_SEMANTICS = "symbolic-literal"

# Literals that may currently be selected for symbolic treatment. The policy is
# expressed as a set of integer values so it can grow without an API change.
_SUPPORTED_SYMBOLIC_LITERALS = frozenset({0, 1})


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
    symbolic_literals: tuple[int, ...] = ()
    rewritable: bool = True
    expanded_replacement: str | None = None
    expanded_from: tuple[str, ...] = ()
    expanded_expression: Expression | None = None

    @property
    def expanded(self) -> bool:
        """True if referenced macros were substituted before simplifying (``expand_macros``)."""
        return bool(self.expanded_from)

    @property
    def semantics(self) -> str:
        """Literal semantics used for this analysis.

        ``"ordinary"`` when integer literals keep their C truth values, or
        ``"symbolic-literal"`` when one or more literals were treated as free
        Boolean atoms (see ``symbolic_literals``).
        """
        return SYMBOLIC_LITERAL_SEMANTICS if self.symbolic_literals else ORDINARY_SEMANTICS

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

    @property
    def edit(self) -> SuggestedEdit | None:
        """Structured edit suggestion when a simpler equivalent expression is proven."""
        if (
            self.simplified
            and self.rewritable
            and self.is_equivalent is True
            and self.complete
            and self.replacement_range is not None
            and self.simplified_replacement is not None
        ):
            return SuggestedEdit(
                range=self.replacement_range,
                replacement=self.simplified_replacement,
                confidence=FixConfidence.EXACT,
            )
        return None


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


def _resolve_symbolic_literals(values: Iterable[int] | None) -> tuple[int, ...]:
    """Validate and normalize a symbolic-literal policy."""
    if values is None:
        return ()
    if isinstance(values, (int, str, bytes)):
        raise AnalysisError(
            "symbolic_literals must be an iterable of integers, for example (0,)",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    try:
        items = tuple(values)
    except TypeError as error:
        raise AnalysisError(
            "symbolic_literals must be an iterable of integers, for example (0,)",
            code=ErrorCode.INVALID_CONFIGURATION,
        ) from error
    supported = ", ".join(str(value) for value in sorted(_SUPPORTED_SYMBOLIC_LITERALS))
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise AnalysisError(
                f"symbolic literal must be an integer, got {item!r}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
        if item not in _SUPPORTED_SYMBOLIC_LITERALS:
            raise AnalysisError(
                f"unsupported symbolic literal {item}; supported literals: {supported}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
    return tuple(sorted(set(items)))


def _symbolize(expression: Expression, literals: tuple[int, ...]) -> Expression:
    """Replace selected literal constants with free ``SymbolicLiteral`` atoms.

    The candidate classifier only admits the literals ``0`` and ``1``, so each
    parsed ``Constant`` corresponds exactly to one of those source literals.
    """
    if not literals:
        return expression
    if isinstance(expression, Constant):
        value = int(expression.value)
        return SymbolicLiteral(str(value)) if value in literals else expression
    if isinstance(expression, Negation):
        return Negation(_symbolize(expression.operand, literals))
    if isinstance(expression, (Conjunction, Disjunction)):
        return type(expression)(
            tuple(_symbolize(operand, literals) for operand in expression.operands)
        )
    return expression


_UNPARENTHESIZED_REASON = (
    "not rewritten: the replacement has a top-level && or || outside parentheses, so "
    "it binds with the operators around each use; wrapping the simplified form in "
    "parentheses would change what those uses mean. Parenthesize the original "
    "replacement to allow the rewrite"
)


def _has_top_level_binary_operator(replacement: str) -> bool:
    """True if ``&&`` or ``||`` appears outside every pair of parentheses.

    Such a replacement is not substitution-safe: ``#define M A || B`` makes
    ``!M`` mean ``!A || B``. Any rewrite adds outer parentheses, which would
    change the meaning of those uses, so it must not be applied.
    """
    operators = top_level_binary_operators([token.text for token in tokens(replacement)])
    return any(operator in ("&&", "||") for operator in operators)


_EXPANSION_REASON = (
    "not rewritten: the result depends on {names}; a macro is bound where it is used, so a "
    "simplification found by expanding other macros is reported but never applied"
)


def _inlining_body(
    definition: MacroDefinition, literals: tuple[int, ...]
) -> Expression | str | None:
    """The expression ``expand_macros`` substitutes for ``definition``'s name.

    Only a Boolean candidate, or a bare identifier alias (``#define ALIAS OTHER``, which
    keeps the value of what it names), is substituted. Every other macro, notably a plain
    constant such as ``#define ENABLED 1``, has a configuration-dependent value and stays
    an opaque atom. A candidate whose replacement has a top-level ``&&`` or ``||`` is
    not substituted either, and the reason is returned: the preprocessor pastes its
    text, so it would bind with the operators around each use.
    """
    is_candidate, _, parsed, _ = classify_macro_candidate(definition)
    if is_candidate and parsed is not None:
        if _has_top_level_binary_operator(definition.replacement):
            return (
                f"{definition.name} is not parenthesized, so its replacement binds with the "
                "operators around each use"
            )
        return _symbolize(parsed, literals)
    if definition.parameters is not None:
        return None
    try:
        toks = tokens(definition.replacement.strip())
    except Exception:
        return None
    if len(toks) == 1 and toks[0].kind == "identifier" and toks[0].text != "defined":
        return Variable(toks[0].text)
    return None


def _with_notes(reason: str | None, notes: Sequence[str]) -> str | None:
    if not notes:
        return reason
    skipped = "not expanded: " + "; ".join(notes)
    return skipped if reason is None else f"{reason} ({skipped})"


def analyze_macro(
    definition: MacroDefinition,
    *,
    options: AnalysisOptions | None = None,
    replacement_range: SourceRange | None = None,
    symbolic_literals: Iterable[int] | None = None,
    expand_macros: Mapping[str, MacroDefinition] | None = None,
) -> MacroAnalysisResult:
    """Analyze one macro definition for Boolean simplification.

    ``symbolic_literals`` opts in to symbolic-literal semantics: each selected
    integer literal (currently ``0`` and ``1``) is treated as a single free
    Boolean atom instead of a fixed truth value. The default keeps ordinary C
    semantics.

    ``expand_macros`` opts in to expansion: a mapping from macro name to the
    :class:`MacroDefinition` that name has, each taken to be the single,
    unconditional definition that precedes ``definition``. Identifiers in the
    replacement list that name a Boolean candidate (or an alias of one) in the
    mapping are replaced by its replacement before simplifying. The result is
    reported but never rewritable. Other names stay opaque atoms.
    """
    literals = _resolve_symbolic_literals(symbolic_literals)
    expander: Expander | None = None
    if expand_macros is not None:
        try:
            inliner = Inliner.from_definitions(
                expand_macros, lambda found: _inlining_body(found, literals)
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise AnalysisError(
                f"expand_macros must map macro names to MacroDefinition objects: {error}",
                code=ErrorCode.INVALID_CONFIGURATION,
            ) from error
        expander = inliner.bind(definition.name, 0, ())
    return _analyze_definition(
        definition,
        options=options,
        replacement_range=replacement_range,
        literals=literals,
        expander=expander,
    )


def _analyze_definition(
    definition: MacroDefinition,
    *,
    options: AnalysisOptions | None,
    replacement_range: SourceRange | None,
    literals: tuple[int, ...],
    expander: Expander | None,
) -> MacroAnalysisResult:
    resolved_options = options or AnalysisOptions()
    if not isinstance(resolved_options, AnalysisOptions):
        raise AnalysisError(
            "options must be an AnalysisOptions instance",
            code=ErrorCode.ANALYSIS_FAILURE,
        )

    is_candidate, reason, parsed, is_wrapped = classify_macro_candidate(definition)
    if not is_candidate or parsed is None:
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=False,
            reason=reason,
            original_replacement=definition.replacement,
            replacement_range=replacement_range,
            symbolic_literals=literals,
        )
    expr = _symbolize(parsed, literals)

    # Candidate expression: apply bounded ROBDD simplification
    limits = resolved_options._resource_limits()
    budget = AnalysisBudget(limits.max_work)
    analysis_expr = expr
    expanded_from: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    try:
        if expander is not None:
            inlined = expander(expr, limits.max_work)
            expanded_from, notes = inlined.expanded, inlined.notes
            if expanded_from:
                analysis_expr = inlined.expression
        atoms = tuple(dict.fromkeys(expression_atoms_in_order(analysis_expr)))
        if len(atoms) > limits.max_atoms:
            raise AnalysisLimitExceeded("atoms", limits.max_atoms, len(atoms))
        bdd = BDD(atoms, limits=limits, budget=budget)
        simplified_expr = exact_simplify(analysis_expr, bdd)
        is_eq = bdd.equivalent_under(TRUE, analysis_expr, simplified_expr)
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
            symbolic_literals=literals,
        )

    expanded_text = f"({format_expression(analysis_expr)})" if expanded_from else None
    expanded_expr = analysis_expr if expanded_from else None

    # The suggestion is the shortest proven-equivalent form, and only if it has fewer
    # tokens than the definition (see cpre/_recomposition.py).
    original_cost = len(tokens(definition.replacement)) + (0 if is_wrapped else 2)
    expansion = (
        _expansion_for(expander, expanded_from, limits.max_work)
        if expander is not None and expanded_from
        else None
    )
    try:
        choice = choose_replacement(
            original=expr,
            original_cost=original_cost,
            expanded=analysis_expr,
            baseline=simplified_expr,
            baseline_proven=is_eq,
            bdd=bdd,
            limits=limits,
            expansion=expansion,
        )
    except AnalysisLimitExceeded:
        choice = None
    if choice is not None and not choice.depends_on_expansion:
        # Found without what the referenced macros stand for: an ordinary result.
        expanded_from, expanded_text, expanded_expr, notes = (), None, None, ()
    if choice is not None:
        # The macro's value stays 0 or 1: a lone identifier is emitted as ``!!X``.
        chosen = keeps_value(choice.expression)
        simplified_replacement = f"({format_expression(chosen)})"
        if expanded_from:
            rewritable = False
            reason = _EXPANSION_REASON.format(names=", ".join(expanded_from))
        else:
            rewritable = not _has_top_level_binary_operator(definition.replacement)
            reason = None if rewritable else _UNPARENTHESIZED_REASON
        return MacroAnalysisResult(
            name=definition.name,
            definition=definition,
            candidate=True,
            reason=_with_notes(reason, notes),
            original_replacement=definition.replacement,
            original_expression=expr,
            simplified_expression=chosen,
            simplified_replacement=simplified_replacement,
            is_equivalent=True,
            incomplete=None,
            replacement_range=replacement_range,
            symbolic_literals=literals,
            rewritable=rewritable,
            expanded_replacement=expanded_text,
            expanded_from=expanded_from,
            expanded_expression=expanded_expr,
        )
    reason = "expression is already in simplest equivalent form"
    if expanded_from:
        reason += f" after expanding {', '.join(expanded_from)}"
    return MacroAnalysisResult(
        name=definition.name,
        definition=definition,
        candidate=True,
        reason=_with_notes(reason, notes),
        original_replacement=definition.replacement,
        original_expression=expr,
        simplified_expression=analysis_expr,
        simplified_replacement=None,
        is_equivalent=is_eq,
        incomplete=None,
        replacement_range=replacement_range,
        symbolic_literals=literals,
        expanded_replacement=expanded_text,
        expanded_from=expanded_from,
        expanded_expression=expanded_expr,
    )


def _expansion_for(
    expander: Expander, expanded_from: tuple[str, ...], max_work: int
) -> Expansion | None:
    """What the recomposition stage may use of the macros ``expander`` substituted."""

    def vocabulary() -> dict[str, Expression]:
        bodies: dict[str, Expression] = {}
        for name in expanded_from:
            try:
                inlined = expander(Variable(name), max_work)
            except AnalysisLimitExceeded:
                continue  # too large to compare with; the macro just is not offered
            if name in inlined.expanded:
                bodies[name] = inlined.expression
        return bodies

    return Expansion(vocabulary, lambda form: expander(form, max_work).expression)


def _to_source_location(loc: object) -> SourceLocation:
    line = getattr(loc, "line", 1) or 1
    column = getattr(loc, "column", None)
    return SourceLocation(line=line, column=column)


@dataclass(frozen=True)
class _PendingObjectMacro:
    """An object-like definition found in the scan, analyzed once the scope is complete."""

    slot: int
    definition: MacroDefinition
    replacement_range: SourceRange | None
    order: int
    context: tuple[tuple[int, int], ...]


MacroProgressCallback = Callable[[int, int, "str | None"], None]
"""``callback(done, total, name)``: ``name`` is about to be analyzed, ``done`` of ``total`` are done.

After the last macro it is called once more with ``done == total`` and ``name`` ``None``."""


def analyze_macros(
    source: str,
    *,
    options: AnalysisOptions | None = None,
    filename: str | None = None,
    symbolic_literals: Iterable[int] | None = None,
    expand_macros: bool = False,
    on_progress: MacroProgressCallback | None = None,
) -> tuple[MacroAnalysisResult, ...]:
    """Analyze all macro definitions in a source string for Boolean simplification.

    ``symbolic_literals`` is forwarded to :func:`analyze_macro`; see that
    function for the opt-in symbolic-literal semantics.

    ``on_progress`` is called as ``on_progress(done, total, name)`` just before each
    object-like macro is analyzed (the part that can take long, especially with
    ``expand_macros``): ``done`` of ``total`` macros are finished and ``name`` is the one
    about to start. After the last one it is called with ``done == total`` and ``name``
    set to ``None``. Function-like macros are not counted; they are analyzed while the
    source is scanned and cost almost nothing. An exception raised by the callback
    propagates, so it can be used to cancel.

    ``expand_macros=True`` opts in to expansion: an identifier in a replacement
    list that names another macro defined in ``source`` is replaced by that
    macro's replacement before simplifying, so ``#define D (A && C)`` with
    ``#define A (B || C)`` simplifies to ``(!!C)``. Only a Boolean candidate (or a
    bare identifier alias of one) is expanded, never a plain constant or other
    non-candidate, because such a macro's value depends on the build
    configuration. A macro is expanded only when it has exactly one ``#define``
    and no ``#undef`` in ``source``, is defined before the macro that uses it, and
    is defined in a conditional block that also encloses that macro; otherwise it
    stays an atom and the result's ``reason`` says why. A result found by
    expansion is reported but never rewritable.
    """
    resolved_options = options or AnalysisOptions()
    literals = _resolve_symbolic_literals(symbolic_literals)
    results: list[MacroAnalysisResult | None] = []
    pending: list[_PendingObjectMacro] = []
    scope = MacroScope()
    tracker = ConditionalTracker()

    for line in logical_lines(source):
        tracker.feed(line)
        undefined = undefined_name(line)
        if undefined is not None:
            scope.undefined.add(undefined)
        directive = line.directive
        if directive is None or directive.name != "define":
            continue
        remainder = line.text[directive.name_end :]
        rem_offset = directive.name_end
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
            else SourceLocation(line.first_line, 1)
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
            scope.add_define(DefineSite(definition, line.index, tracker.context))
            results.append(
                _analyze_definition(
                    definition,
                    options=resolved_options,
                    replacement_range=None,
                    literals=literals,
                    expander=None,
                )
            )
            continue

        # Object-like macro
        if tail and not tail[0].isspace():
            definition = MacroDefinition(
                name=name,
                replacement=tail.strip(),
                location=name_location,
            )
            scope.add_define(DefineSite(definition, line.index, tracker.context))
            results.append(
                MacroAnalysisResult(
                    name=name,
                    definition=definition,
                    candidate=False,
                    reason="object-like macro replacement requires whitespace after name",
                    original_replacement=tail.strip(),
                    symbolic_literals=literals,
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
        scope.add_define(DefineSite(definition, line.index, tracker.context))
        pending.append(
            _PendingObjectMacro(
                slot=len(results),
                definition=definition,
                replacement_range=replacement_range,
                order=line.index,
                context=tracker.context,
            )
        )
        results.append(None)

    # Object-like definitions are analyzed once every #define and #undef is known, so
    # expansion can tell whether a referenced macro is defined exactly once.
    inliner = (
        Inliner(scope, lambda found: _inlining_body(found, literals)) if expand_macros else None
    )
    total = len(pending)
    for done, item in enumerate(pending):
        if on_progress is not None:
            on_progress(done, total, item.definition.name)
        results[item.slot] = _analyze_definition(
            item.definition,
            options=resolved_options,
            replacement_range=item.replacement_range,
            literals=literals,
            expander=(
                inliner.bind(item.definition.name, item.order, item.context)
                if inliner is not None
                else None
            ),
        )
    if on_progress is not None:
        on_progress(total, total, None)

    return tuple(result for result in results if result is not None)


@dataclass(frozen=True)
class MacroSimplificationResult:
    """Result of macro simplification analysis and optional source rewriting."""

    source: str
    rewritten_source: str
    results: tuple[MacroAnalysisResult, ...]
    rewritten: bool
    applied_count: int
    verified: bool
    filename: str | None = None

    @property
    def simplifications(self) -> tuple[MacroAnalysisResult, ...]:
        """Tuple of results that represent simplified macros."""
        return tuple(r for r in self.results if r.simplified)

    @property
    def has_findings(self) -> bool:
        """True if any simplifiable macro was found."""
        return any(r.simplified for r in self.results)


def _location_to_offset(line_starts: list[int], source_len: int, location: SourceLocation) -> int:
    line_idx = location.line - 1
    if line_idx < 0:
        return 0
    if line_idx >= len(line_starts):
        return source_len
    start = line_starts[line_idx]
    col = location.column or 1
    return min(start + max(0, col - 1), source_len)


def _apply_and_verify_rewrites(
    source: str,
    results: Sequence[MacroAnalysisResult],
    *,
    symbolic_literals: tuple[int, ...],
    options: AnalysisOptions | None,
    filename: str | None,
) -> tuple[str, bool, int, bool]:
    """Apply proven-equivalent simplifications to source and verify the result.

    Returns (rewritten_source, was_rewritten, applied_count, verified).
    """
    candidates_to_rewrite = [
        (idx, r)
        for idx, r in enumerate(results)
        if r.simplified
        and r.is_equivalent is True
        and r.complete
        and r.edit is not None
        and r.replacement_range is not None
        and r.simplified_replacement is not None
    ]

    if not candidates_to_rewrite:
        return source, False, 0, True

    line_starts = _line_starts(source)
    source_len = len(source)
    edits_with_offsets: list[tuple[MacroAnalysisResult, int, int]] = []

    for _idx, r in candidates_to_rewrite:
        edit = r.edit
        assert edit is not None
        start_offset = _location_to_offset(line_starts, source_len, edit.range.start)
        end_offset = _location_to_offset(line_starts, source_len, edit.range.end)
        if not (0 <= start_offset < end_offset <= source_len):
            raise AnalysisError(
                f"Invalid source range for macro '{r.name}': {edit.range}",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        edits_with_offsets.append((r, start_offset, end_offset))

    # Sort descending by start offset so applying earlier edits does not shift subsequent ranges
    edits_with_offsets.sort(key=lambda item: item[1], reverse=True)

    # Check for overlapping ranges
    for i in range(len(edits_with_offsets) - 1):
        curr_r, curr_start, curr_end = edits_with_offsets[i]
        next_r, next_start, next_end = edits_with_offsets[i + 1]
        if next_end > curr_start:
            raise AnalysisError(
                f"Overlapping macro replacements between '{next_r.name}' and '{curr_r.name}'",
                code=ErrorCode.ANALYSIS_FAILURE,
            )

    rewritten = source
    for r, start_offset, end_offset in edits_with_offsets:
        assert r.simplified_replacement is not None
        rewritten = rewritten[:start_offset] + r.simplified_replacement + rewritten[end_offset:]

    # Reparse and reanalyze the resulting definition for verification
    re_results = analyze_macros(
        rewritten,
        options=options,
        filename=filename,
        symbolic_literals=symbolic_literals,
    )

    if len(re_results) != len(results):
        raise AnalysisError(
            f"Rewrite verification failed: expected {len(results)} macros after rewrite, found {len(re_results)}",
            code=ErrorCode.ANALYSIS_FAILURE,
        )

    for idx, r in candidates_to_rewrite:
        re_r = re_results[idx]
        if re_r.name != r.name:
            raise AnalysisError(
                f"Rewrite verification failed: macro '{r.name}' not found after rewrite",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        if not re_r.complete or re_r.incomplete is not None:
            raise AnalysisError(
                f"Rewrite verification failed for '{r.name}': re-analysis was incomplete ({re_r.reason})",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        if re_r.simplified:
            raise AnalysisError(
                f"Rewrite verification failed for '{r.name}': definition is still simplifiable "
                f"({re_r.original_replacement} -> {re_r.simplified_replacement})",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        if re_r.original_replacement != r.simplified_replacement:
            raise AnalysisError(
                f"Rewrite verification failed for '{r.name}': expected replacement "
                f"{r.simplified_replacement!r}, found {re_r.original_replacement!r}",
                code=ErrorCode.ANALYSIS_FAILURE,
            )
        if r.original_expression is not None and re_r.original_expression is not None:
            atoms = tuple(
                dict.fromkeys(
                    list(expression_atoms_in_order(r.original_expression))
                    + list(expression_atoms_in_order(re_r.original_expression))
                )
            )
            limits = (options or AnalysisOptions())._resource_limits()
            budget = AnalysisBudget(limits.max_work)
            bdd = BDD(atoms, limits=limits, budget=budget)
            if not bdd.equivalent_under(TRUE, r.original_expression, re_r.original_expression):
                raise AnalysisError(
                    f"Rewrite verification failed: '{r.name}' rewritten expression is not equivalent to original",
                    code=ErrorCode.ANALYSIS_FAILURE,
                )

    return rewritten, True, len(candidates_to_rewrite), True


def simplify_macros(
    source: str,
    *,
    filename: str | None = None,
    rewrite: bool = False,
    symbolic_literals: Iterable[int] | None = None,
    options: AnalysisOptions | None = None,
    expand_macros: bool = False,
    on_progress: MacroProgressCallback | None = None,
) -> MacroSimplificationResult:
    """Analyze macro definitions for Boolean simplification, and optionally rewrite source.

    ``on_progress`` reports how far the analysis is; see :func:`analyze_macros`. It is not
    called for the verification that follows a rewrite.

    When ``rewrite=False`` (the default), source is never modified.
    When ``rewrite=True``, only transformations proven equivalent under the
    selected semantic mode are applied, and the result is verified by
    re-parsing and re-analyzing the rewritten source.

    ``expand_macros=True`` reports simplifications found by expanding referenced
    Boolean macros (see :func:`analyze_macros`). Those results are never applied,
    so combining it with ``rewrite=True`` raises :class:`AnalysisError` with
    ``ErrorCode.INVALID_CONFIGURATION``.
    """
    if expand_macros and rewrite:
        raise AnalysisError(
            "expand_macros cannot be combined with rewrite: a simplification found by "
            "expanding other macros is reported but never applied",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    resolved_options = options or AnalysisOptions()
    literals = _resolve_symbolic_literals(symbolic_literals)
    results = analyze_macros(
        source,
        options=resolved_options,
        filename=filename,
        symbolic_literals=literals,
        expand_macros=expand_macros,
        on_progress=on_progress,
    )

    if not rewrite:
        return MacroSimplificationResult(
            source=source,
            rewritten_source=source,
            results=results,
            rewritten=False,
            applied_count=0,
            verified=True,
            filename=filename,
        )

    rewritten_source, was_rewritten, count, verified = _apply_and_verify_rewrites(
        source,
        results,
        symbolic_literals=literals,
        options=resolved_options,
        filename=filename,
    )

    return MacroSimplificationResult(
        source=source,
        rewritten_source=rewritten_source,
        results=results,
        rewritten=was_rewritten,
        applied_count=count,
        verified=verified,
        filename=filename,
    )


def rewrite_macros(
    source: str,
    *,
    filename: str | None = None,
    symbolic_literals: Iterable[int] | None = None,
    options: AnalysisOptions | None = None,
) -> str:
    """Convenience function to rewrite simplifiable macros in source text.

    Requires all applied transformations to be proven equivalent and verified.
    Returns the rewritten source string.
    """
    result = simplify_macros(
        source,
        filename=filename,
        rewrite=True,
        symbolic_literals=symbolic_literals,
        options=options,
    )
    return result.rewritten_source


__all__ = [
    "MacroAnalysisResult",
    "MacroSimplificationResult",
    "analyze_macro",
    "analyze_macros",
    "classify_macro_candidate",
    "rewrite_macros",
    "simplify_macros",
]
