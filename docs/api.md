# Python API integration guide

This guide is for tools, linters, CI integrations, and scripts that want to use `cpre` as a library rather than parse its CLI output.

For SARIF-specific integration, see [SARIF output](sarif.md). The package README remains the quick-start entry point; this document describes the programmatic contract in more detail.

## Basic analysis

Import supported symbols from the top-level `cpre` package:

```python
import cpre

result = cpre.analyze_source(source_text, filename="src/example.c")
```

`filename` is metadata only. It does not affect Boolean reasoning, but downstream tools should supply it so findings and diagnostics retain source identity.

A successful `AnalysisResult` contains:

- `findings`: an ordered tuple of structured `Finding` objects.
- `tree`: the analyzed conditional tree for callers that need structural context.
- `filename`: the optional source identity supplied by the caller.
- `complete`: `True` when exact analysis completed within configured resource limits.
- `incomplete`: structured diagnostics explaining why exact analysis was curtailed.

Do not infer success from an empty `findings` tuple alone. Always check `result.complete` before treating an analysis as clean.

```python
result = cpre.analyze_source(source_text, filename=path)
if not result.complete:
    for diagnostic in result.incomplete:
        handle_incomplete(path, diagnostic)
    return

for finding in result.findings:
    handle_finding(path, finding)
```

When analysis is incomplete, cpre deliberately emits no findings based on partial proofs.

## Finding kinds

`Finding.kind` is a `FindingKind` enum. Handle enum members rather than matching rendered messages:

```python
for finding in result.findings:
    match finding.kind:
        case cpre.FindingKind.DEAD_BRANCH:
            report_dead_branch(finding)
        case cpre.FindingKind.REDUNDANT_BRANCH:
            report_redundant_branch(finding)
        case cpre.FindingKind.SIMPLIFIABLE_CONDITION:
            report_exact_simplification(finding)
        case cpre.FindingKind.CONTEXTUAL_SIMPLIFICATION:
            report_contextual_simplification(finding)
```

Each finding includes a one-based `location`, the directive kind, the original condition when applicable, a human-readable `reason`, and any opaque predicates retained by the Boolean model.

`depends_on_assumptions` identifies findings whose proof materially depends on caller-supplied macro assumptions. This is useful when a parent analyzer needs to distinguish configuration-independent results from configuration-specific results.

## Simplifications and edits

Exact and contextual simplifications have intentionally different guarantees:

- `ExactSimplification` is Boolean-equivalent to the original condition under the active assumptions.
- `ContextualSimplification` is equivalent only in the branch's reachable context.

Prefer the typed fields over compatibility convenience properties:

```python
if finding.exact_simplification is not None:
    replacement = finding.exact_simplification.replacement

if finding.contextual_simplification is not None:
    contextual_replacement = finding.contextual_simplification.replacement
```

When cpre can describe a safe direct source replacement, `finding.edit` contains a `SuggestedEdit`:

```python
edit = finding.edit
if edit is not None:
    start = edit.range.start
    end = edit.range.end
    replacement = edit.replacement
    confidence = edit.confidence
```

`SourceRange.end` is exclusive. Locations are physical, one-based source positions. Backslash-continued preprocessor directives retain physical line/column information.

Dead and redundant branch findings intentionally do not imply a mechanical deletion. Consumers should not synthesize destructive fixes merely because `finding.edit` is absent.

## Macro assumptions

The default analysis is symbolic and configuration-independent. Callers with known build configuration may constrain the analysis.

For simple Boolean values:

```python
result = cpre.analyze_source(
    source_text,
    filename=path,
    assumptions={"FEATURE_A": True, "FEATURE_B": False},
)
```

Use `MacroAssumptions` when definedness and Boolean value need to be modeled independently:

```python
assumptions = cpre.MacroAssumptions(
    defined={"FEATURE_A", "FEATURE_ZERO"},
    undefined={"FEATURE_B"},
    values={"FEATURE_A": True, "FEATURE_ZERO": False},
)

result = cpre.analyze_source(source_text, assumptions=assumptions)
```

Unknown macros remain symbolic. cpre does not silently treat unmentioned macros as false.

## Resource limits

ROBDD reasoning is bounded deterministically. Defaults are intended to be conservative for static-analysis use, and callers can provide explicit limits:

```python
options = cpre.AnalysisOptions(
    max_atoms=64,
    max_bdd_nodes=100_000,
    max_work=500_000,
)
result = cpre.analyze_source(source_text, filename=path, options=options)
```

If a limit is exceeded, `analyze_source` returns an incomplete `AnalysisResult` rather than raising an exception or returning partial findings:

```python
if not result.complete:
    diagnostic = result.incomplete[0]
    print(
        diagnostic.code,
        diagnostic.resource,
        diagnostic.limit,
        diagnostic.observed,
        diagnostic.location,
    )
```

A downstream analyzer should propagate this distinction. Treating incomplete analysis as "no findings" can create false confidence.

## Structured errors

Malformed conditional source and invalid API input use the public `CpreError` hierarchy:

```python
try:
    result = cpre.analyze_source(source_text, filename=path)
except cpre.CpreError as error:
    handle_error(
        code=error.code,
        message=error.message,
        location=error.location,
        filename=error.filename,
    )
```

Use `ErrorCode` values rather than parsing `str(error)` or human-readable messages. Stable codes include syntax/directive errors, invalid assumptions, bounded-analysis failures, and `SOURCE_READ_ERROR` for tool-level source ingestion reporting.

Note that `analyze_source` accepts source text; it does not open files itself. `SOURCE_READ_ERROR` is primarily used by the CLI/SARIF integration layer. Library callers that read files are responsible for converting their own I/O failures into the diagnostic model appropriate for their host tool.

Unexpected programming errors are intentionally not collapsed into `CpreError`.

## Integrating into another analyzer

A typical static-analysis integration should keep cpre as a focused conditional-analysis component:

```python
import cpre


def analyze_preprocessor_conditions(path: str, source: str):
    try:
        result = cpre.analyze_source(source, filename=path)
    except cpre.CpreError as error:
        return {
            "status": "error",
            "code": error.code.value,
            "location": error.location,
            "message": error.message,
        }

    if not result.complete:
        return {
            "status": "incomplete",
            "diagnostics": result.incomplete,
        }

    return {
        "status": "complete",
        "findings": result.findings,
    }
```

Recommended integration rules:

1. Read source and own file-system policy in the host tool.
2. Call `cpre.analyze_source` once per source input.
3. Catch only `CpreError` as supported cpre failures.
4. Check `result.complete` before consuming findings.
5. Map `FindingKind` directly to the host tool's rule identifiers.
6. Preserve `depends_on_assumptions` when configuration-specific reasoning is enabled.
7. Apply only explicit `SuggestedEdit` objects as automatic source replacements.
8. Do not import private modules such as `cpre.robdd`, `cpre.parser`, or the compatibility facade internals.

For a tool such as cgull, this keeps ownership clean: cgull can provide source identity/configuration, invoke cpre, translate cpre findings into its own issue model, and preserve cpre's complete/incomplete distinction without depending on cpre's CLI or internal ROBDD representation.

## API compatibility

Supported integrations should import from `cpre`, not internal modules. The top-level `__all__` is the compatibility boundary for public symbols.

The CLI JSON format is a structural conditional-tree report and should not be used as the library API. If a process-to-process interchange format is required, prefer [SARIF output](sarif.md) for findings or call the Python API directly when both components run in Python.

## Concrete conditional selection

`preprocess_source` selects one configuration and expands supported object-like
and function-like macros without reading headers. Always check `complete` before
passing its output to a downstream parser:

```python
from cpre import MacroAssumptions, preprocess_source

result = preprocess_source(
    "#ifdef FEATURE\nint enabled;\n#else\nint disabled;\n#endif\n",
    filename="example.c",
    assumptions=MacroAssumptions(defined={"FEATURE"}),
)
if result.complete:
    parse_translation_unit(result.source, filename=result.filename)
else:
    for diagnostic in result.incomplete:
        report(diagnostic.code, diagnostic.location, diagnostic.message)
```

The public `PreprocessResult` contains `source`, `filename`, `incomplete`, `macros`,
`source_map`, and a
`complete` property. `source` is `None` whenever selection is incomplete; partial
selection is never exposed. Diagnostics are ordered by source line and use either
`PreprocessDiagnostic` or the existing `AnalysisIncomplete` resource diagnostic.
Malformed conditional directives raise structured `ParseError`, with the same
codes, physical locations, and filename metadata as `analyze_source`.

On success, conditional directives (including all continuation lines) and inactive
text become spaces. Block comments overlapping retained text are kept whole to
balance delimiters across selected lines; comments wholly in discarded regions
stay masked. Physical line endings and the presence or absence of a final newline
are preserved. Macro expansion changes text length and columns; use
`source_map` for physical source coordinates. Unexpanded spans retain their text
and map one-to-one. Nested `#if`, `#ifdef`, `#ifndef`,
`#elif`, `#elifdef`, `#elifndef`, `#else`, and `#endif` are supported.

Assumptions accept the same `MacroAssumptions` or Boolean mapping as
`analyze_source`: mappings constrain Boolean values; definedness alone does not
imply a nonzero value; undefined macros have false values. This concrete API always
distinguishes definedness from value, including when assumptions are omitted
(unlike the legacy symbolic analysis mode). Unmentioned macros and opaque
arithmetic/comparison predicates remain unknown. If they affect a reachable branch
choice, `unresolved_condition` marks the result incomplete. Tautologies and
unreachable unknown conditions do not require extra assumptions.

Active `#define` and `#undef` directives update a per-run `MacroEnvironment` in
source order and are masked in the output. Changes inside discarded branches do
not affect state. Each active redefinition replaces the previous entry, including
assumption-seeded state; `#undef` records known-undefined/false state even for an
unmentioned macro. Earlier conditions are never reevaluated after a later change.

```python
result = preprocess_source("#define COUNT 0\n#ifdef COUNT\nint enabled;\n#endif\n")
assert result.complete
state = result.macros["COUNT"]
assert state.defined is True and state.value is False
assert state.definition.numeric_value == 0
```

The public `MacroDefinition`, `MacroState`, and `MacroEnvironment` types live in
`cpre.macros` and are exported from `cpre`. `MacroDefinition` stores the name,
comment-stripped logical replacement text, physical definition location, parameter
tuple, and variadic flag. `parameters=None` means object-like; `parameters=()` means
a function-like macro with no named parameters. Standard trailing `...` is supported.
Definitions retain unexpanded replacement text (not a byte-for-byte copy of
continued source); ordinary active source uses the definitions current at each use. `MacroState.defined` and `.value` are independent optional
Booleans: `None` means unknown. Assumption-only entries have no source definition.

Object-like integer literals (decimal, octal, hexadecimal, with suffixes, optional
sign and enclosing parentheses) expose `numeric_value` and determine Boolean
truth. Empty replacements are known-defined but have unknown truth. Aliases and compound replacement expressions expand in ordinary source, but
conditions requiring their values remain unresolved; expansion inside conditional
expressions is not part of this API increment. A bare function-like macro
name has false value because it is not invoked. Numeric comparisons remain opaque,
even when the operand macro has a known integer value.

`MacroEnvironment(assumptions)` provides `get(name)`, `define(MacroDefinition(...))`,
`undef(name)`, and `snapshot()` for reuse by downstream consumers. `get` returns an
unknown state for unmentioned names. Snapshots contain only explicitly tracked
names in sorted order and are detached read-only mappings of immutable entries.
`PreprocessResult.macros` is the final snapshot on success, including an empty
mapping for an empty environment; it is `None` on incomplete results. This API does
not currently expose point-in-source snapshots. Directive parsing and Boolean
resolution remain internal; the symbolic `analyze_source` API is unchanged.

Active `#include`, `#include_next`, and `#import` directives produce
`unsupported_preprocessing_directive`. Malformed or unsupported active macro
definitions produce the same diagnostic. Reachable nonconditional directives outside
the supported conditional/definition set, including `#line`, `#pragma`, `#error`,
`#warning`, and implementation-specific directives, also produce
`unsupported_preprocessing_directive`. A null `#` directive is harmless and masked.
Directives in discarded branches do not block selection. `#error` and `#warning`
are represented only by the returned structured diagnostic; the library does not
write them directly to stderr. Include processing remains unsupported; standard
stringification and token pasting are supported as described below.

Known predefined macros whose values cpre does not model deterministically, including
`__LINE__`, `__FILE__`, `__DATE__`, `__TIME__`, `__COUNTER__`, and standard `__STDC*`
names, produce `unsupported_macro_expansion` when they are reachable without an
explicit concrete replacement definition. This applies in ordinary source, reachable
conditional expressions, and when another macro expands to one of these names.
Ordinary unknown C identifiers remain valid and unchanged; matching spellings inside
comments or string/character literals are not treated as macro uses. A concrete
`MacroConfiguration` definition may supply replacement text for an environment macro;
Boolean assumptions alone do not invent replacement text.

All of these unsupported cases are atomic. `source`, `source_map`, and `macros` are
`None`, so callers cannot accidentally consume a partially transformed translation
unit or a fabricated mapping for semantics cpre did not perform. `complete=True`
therefore certifies the documented supported selection/expansion surface and no known
#38 built-in/directive blocker, but it still does not certify general GCC/Clang
preprocessing equivalence or arbitrary implementation-specific extensions.
`AnalysisOptions` supplies the same resource limits as analysis; limit exhaustion
also returns no source or macro snapshot. The bounded migration status is tracked by
the [C-GULL replacement gate](pcpp-readiness.md).


### Object-like macro expansion (0.10.0)

Active ordinary-source identifiers expand recursively using the current macro
environment. Redefinitions affect subsequent uses; discarded branches do not
change expansion. Ordinary undefined and unmentioned identifiers remain unchanged;
known unmodeled predefined macro names are diagnosed as described above. Comments,
quoted literals (including encoding prefixes and raw strings), and preprocessing
numbers are protected. Replacement-token separators prevent accidental identifier,
operator, or comment formation; whitespace is not intended to match compiler `-E`
formatting. Empty macros produce separating whitespace.

A macro is disabled while its replacement is rescanned: `A -> A` and `A -> B -> A`
terminate with the suppressed identifier retained, matching C recursion suppression.
The implementation uses an explicit stack and the shared `max_work` budget, including
emitted replacement characters. Limit exhaustion returns no source, map, or macro
snapshot. Invalid replacement operations and assumption-only macros without
replacement text return `unsupported_macro_expansion`. Standard `#`/`##`
operations, including digraph spellings, are described below. Bare function-like names can remain in output;
unused unsupported definitions and inactive uses do not block preprocessing.
Boolean assumptions are not guessed to mean literal `0` or `1` replacement text.

```python
result = preprocess_source("#define N 12345\nint a[N];\n", filename="example.c")
assert result.complete
for span in result.source_map:
    if span.expanded:
        assert result.source[span.output_start:span.output_end].strip() == "12345"
        assert span.start.line == 2  # the invocation, not the definition
```

`SourceMapping` is a frozen public dataclass exported from `cpre`. Its
`output_start`/`output_end` and `source_start`/`source_end` are zero-based,
half-open Python character offsets, not byte offsets. `start`/`end` are one-based
physical `SourceLocation` values, also end-exclusive. The ordered tuple covers
all output characters. For `expanded=False`, map an output offset by adding its
displacement within the span to `source_start`. For `expanded=True`, all characters
(including separating whitespace) map to the original invocation's physical range.
Nested replacements map to the outer source invocation. Spliced invocations can
cover several physical lines; their line endings are retained after the replacement.
`source_map` is `()` for empty input and `None` on incomplete results.

This deliberately changes the 0.9.x guarantee of unchanged retained text and
columns. Consumers of expanded output must use the map rather than equating output
columns with original columns. The symbolic `analyze_source` API is unchanged.


### Function-like macro expansion (0.10.1)

Function-like macros expand when their identifier is followed by a `(`
preprocessing token; comments, spaces, and physical newlines may intervene.
Arguments are collected **before expansion**: only commas at parenthesis depth
zero separate arguments. Parentheses inside literals and comments do not count.
Braces, brackets, and C++ template angle brackets do not protect commas; callers
must add parentheses where needed. A bare function-like macro name remains text.
Expanding a later token into `(` does not retroactively invoke an earlier name.

Each used argument is fully macro-expanded before substitution, then the
substituted replacement is rescanned together with following source tokens.
Unused arguments are not expanded. For example, `F(F(1))` expands its inner call
before substituting into the outer call. Recursion suppression stays attached to
tokens, so direct and mutual recursion terminate and suppressed names are not
incorrectly reenabled by argument substitution. Both argument prescan and body
rescan use an explicit stack with the shared deterministic work budget.

```python
source = "#define ADD(a,b) ((a)+(b))\nint n = ADD(1, ADD(2,3));\n"
result = preprocess_source(source)
assert result.complete
span, = [span for span in result.source_map if span.expanded]
assert source[span.source_start:span.source_end] == "ADD(1, ADD(2,3))"
```

Zero-parameter macros (`F()`), empty positional arguments (`F(,x)`), multiline
replacement definitions, and multiline invocations are supported. Standard
trailing `...` parameters substitute their comma-separated tokens through
`__VA_ARGS__`. A variadic-only `V()` supplies empty variadic tokens. For a macro
with named parameters plus `...`, supply the separating comma even when the
variadic part is empty: `V(x,)`. Omitted variadic arguments, `__VA_OPT__`, and GNU
named variadic parameters remain unsupported. Standard stringification and token
pasting are supported as described below.

Arity mismatches, unterminated calls, unsupported replacement operations, and
invocations interrupted by preprocessing directives return structured incomplete
results without partial source, macro snapshots, or source maps. A call may span
ordinary physical lines, but directives between its name and closing parenthesis
are not supported. Definitions in inactive branches still have no effect, and
ordinary redefinitions affect only following uses. Expansion is not added to
`#if`/`#elif` expressions by this release.

Expanded map ranges cover the complete physical invocation, including its closing
parenthesis. Nested expansions map to the enclosing invocation; aliases which
consume following source arguments extend the mapped range to include them.
Physical line endings consumed by a multiline invocation are retained after its
replacement, keeping later physical lines aligned. Comments outside consumed
invocations remain unchanged; comments inside arguments are preprocessing
whitespace and disappear with the invocation.

### Stringification and token pasting (0.10.2)

`#parameter` stringifies the unexpanded argument, trims leading/trailing whitespace,
collapses internal preprocessing whitespace, and escapes quotes and backslashes
inside string/character literals. `##` substitutes adjacent arguments without
prescan, joins the bordering tokens, validates that the result is one preprocessing
token, and rescans it. Empty arguments use placemarker behavior. The digraphs `%:`
and `%:%:` have the same operator roles. Normal parameter uses still expand before
substitution; two-level wrapper macros can therefore request expansion before
stringification or pasting.

Standard `__VA_ARGS__` works with these operators. GNU comma swallowing and
`__VA_OPT__` are outside the supported contract. Invalid pastes and malformed
operator placement return `unsupported_macro_expansion` atomically when used.
The compatibility corpus checks representative operator output against pcpp;
it does not claim full GCC/Clang compatibility.