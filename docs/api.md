# Python API integration guide

Use the top-level `cpre` package as the supported Python compatibility boundary. This guide covers symbolic conditional analysis and the general integration contract. Concrete configuration selection and macro expansion live in the separate [preprocessing guide](preprocessing.md).

For command-line workflows, see the [CLI guide](cli.md). For process-to-process findings interchange, see [SARIF output](sarif.md).

## Supported import boundary

Import public symbols from `cpre`:

```python
import cpre

result = cpre.analyze_source(source_text, filename="src/example.c")
```

The names exported by `cpre.__all__` are the supported public boundary. Internal implementation modules such as `cpre.robdd`, `cpre.parser`, `cpre.model`, and `cpre.expressions` are not downstream APIs.

The public surface includes the symbolic expression model/algebra, symbolic analysis result/error model, source locations and edits, macro assumptions/configuration types, and concrete preprocessing types. This guide focuses on `analyze_source()` and the reusable symbolic expression surface; see [Concrete preprocessing](preprocessing.md) for `preprocess_source()`, `PreprocessResult`, `compact()`, macro environments, deterministic preprocessing context, and pragma handling.

## Symbolic expression API

Downstream analyzers that need configuration-independent Boolean structure can construct and inspect expressions using top-level `cpre` imports only:

```python
import cpre

condition = cpre.conjunction(
    cpre.Variable("FEATURE"),
    cpre.DefinedVariable("CONFIG"),
    cpre.Predicate("VERSION >= 4"),
)

print(cpre.format_expression(condition))
```

The public node categories are:

- `Constant` / `TRUE` / `FALSE` for Boolean constants;
- `Variable(name)` for the truth/value of a macro;
- `DefinedVariable(name)` for macro definedness;
- `Predicate(text)` for an opaque C/preprocessor predicate that cpre does not interpret as an integer expression in this symbolic model;
- `Negation`, `Conjunction`, and `Disjunction` for Boolean structure;
- `Expression` and `BooleanAtom` as public typing aliases for those categories.

`Variable("A")`, `DefinedVariable("A")`, and `Predicate("A")` are intentionally different atoms even though two of them may render to the same text. Do not infer atom identity from rendered strings or class module paths.

### Deterministic algebra and formatting

Use the public helpers instead of depending on internal expression functions:

```python
a = cpre.Variable("A")
b = cpre.Variable("B")

expr = cpre.conjunction(a, a, b)
assert cpre.normalize(expr) == cpre.conjunction(a, b)
assert cpre.disjunction(a, cpre.negate(a)) == cpre.TRUE
```

`normalize()` and `simplify()` are equivalent public entry points for local Boolean normalization. They flatten associative nodes, remove identities and duplicates, detect complements, apply simple absorption, and order operands deterministically. They do **not** perform ROBDD/SAT proofs or evaluate opaque predicates.

`format_expression()` normalizes before rendering, so equivalent local structure has deterministic preprocessor-style output. `ordered_atoms()` returns every unique atom referenced by the original expression in deterministic semantic order; it intentionally inventories unsimplified input rather than dropping atoms eliminated by normalization. `expression_predicates()` returns the opaque predicate text set.

### Structured expression interchange

Use `expression_to_dict()` and `expression_from_dict()` when symbolic expressions need to cross cache/profile/artifact boundaries:

```python
encoded = cpre.expression_to_dict(condition)
restored = cpre.expression_from_dict(encoded)
assert restored == cpre.normalize(condition)
```

The representation is JSON-compatible and explicitly tagged by semantic node category (`constant`, `variable`, `defined`, `predicate`, `not`, `and`, `or`). Serialization is canonical: expressions are normalized first and operand order is deterministic. Deserialization rejects unknown kinds, missing/extra fields, and values with the wrong JSON shape rather than coercing them.

The structured form, not dataclass `repr()`, implementation-module paths, or incidental hash/set ordering, is the supported interchange representation. Patch releases preserve these public categories and tagged semantics; intentional incompatible changes follow the downstream compatibility policy described below.

## Basic analysis

```python
import cpre

try:
    result = cpre.analyze_source(source_text, filename="src/example.c")
except cpre.CpreError as error:
    handle_error(error)
else:
    if not result.complete:
        handle_incomplete(result.incomplete)
    else:
        for finding in result.findings:
            handle_finding(finding)
```

`filename` is source identity metadata. Supplying it is recommended so findings and diagnostics remain associated with their originating file.

A successful `AnalysisResult` exposes:

- `findings`: ordered structured `Finding` values;
- `tree`: the analyzed conditional tree for consumers that need structural context;
- `filename`: caller-supplied source identity;
- `complete`: whether exact analysis completed within the configured contract and limits;
- `incomplete`: structured diagnostics explaining why exact analysis did not complete.

Do not infer success from an empty findings tuple. Always check `result.complete` before treating analysis as clean.

When analysis is incomplete, cpre deliberately does not expose findings based on partial proofs.

## Finding kinds

`Finding.kind` is a `FindingKind` enum. Match enum members rather than rendered messages:

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

Findings carry physical source location, directive/condition context, a human-readable reason, and structured simplification/edit fields where applicable. `depends_on_assumptions` identifies proofs that materially depend on caller-supplied macro assumptions.

Treat human-readable messages as presentation. Use enum values and structured fields for automation.

## Exact and contextual simplifications

cpre deliberately distinguishes two guarantees:

- `ExactSimplification` is Boolean-equivalent to the original condition under the active assumptions.
- `ContextualSimplification` is equivalent only inside that branch's reachable context, including enclosing and preceding branch conditions.

Prefer the typed fields:

```python
if finding.exact_simplification is not None:
    replacement = finding.exact_simplification.replacement

if finding.contextual_simplification is not None:
    replacement = finding.contextual_simplification.replacement
```

A consumer should preserve the distinction rather than presenting contextual replacements as globally equivalent expressions.

## Suggested edits and source ranges

When cpre can describe a safe direct source replacement, `finding.edit` is a `SuggestedEdit`:

```python
edit = finding.edit
if edit is not None:
    start = edit.range.start
    end = edit.range.end
    replacement = edit.replacement
    confidence = edit.confidence
```

`SourceRange.end` is exclusive. Locations are physical, one-based source positions. Backslash-continued directives retain physical line/column provenance.

Dead/redundant branch findings intentionally do not imply mechanical branch deletion. Do not synthesize destructive fixes merely because a branch is unreachable or redundant; apply automatic changes only when an explicit structured edit is present and suitable for the host tool's policy.

## Macro assumptions

Without assumptions, symbolic analysis remains configuration-independent. Callers with known build state may constrain the proof.

For simple Boolean values:

```python
result = cpre.analyze_source(
    source_text,
    filename=path,
    assumptions={
        "FEATURE_A": True,
        "FEATURE_B": False,
    },
)
```

Use `MacroAssumptions` when definedness and Boolean value must be represented separately:

```python
assumptions = cpre.MacroAssumptions(
    defined={"FEATURE_A", "FEATURE_ZERO"},
    undefined={"FEATURE_B"},
    values={
        "FEATURE_A": True,
        "FEATURE_ZERO": False,
    },
)

result = cpre.analyze_source(source_text, assumptions=assumptions)
```

Unmentioned macros remain symbolic. cpre does not silently treat unknown names as false in symbolic analysis.

Use `Finding.depends_on_assumptions` when the host needs to distinguish configuration-independent findings from findings proven only under supplied assumptions.

For exact external macro replacement definitions and open/closed unknown-name policy during concrete preprocessing, use `MacroConfiguration` as described in [Concrete macro configuration](concrete-configuration.md) and [Concrete preprocessing](preprocessing.md).

## Deterministic resource limits

ROBDD reasoning is bounded deterministically. Callers may provide explicit limits:

```python
options = cpre.AnalysisOptions(
    max_atoms=64,
    max_bdd_nodes=100_000,
    max_work=500_000,
)

result = cpre.analyze_source(
    source_text,
    filename=path,
    options=options,
)
```

If a limit is exceeded, `analyze_source()` returns an incomplete `AnalysisResult` rather than raising solely because the configured proof budget was exhausted or returning partial findings.

```python
if not result.complete:
    for diagnostic in result.incomplete:
        print(
            diagnostic.code,
            diagnostic.resource,
            diagnostic.limit,
            diagnostic.observed,
            diagnostic.location,
        )
```

Downstream tools should preserve this distinction. Converting incomplete analysis into "no findings" creates false confidence.

## Structured errors

Malformed conditional source and invalid public API input use the `CpreError` hierarchy:

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

Use `ErrorCode` values and structured locations rather than parsing `str(error)` or terminal text. Unexpected programming errors are intentionally not collapsed into `CpreError`.

`analyze_source()` accepts source text and does not open files. File I/O policy belongs to the host application. `SOURCE_READ_ERROR` is primarily used by cpre's CLI/SARIF ingestion layer; library callers should map their own I/O failures into the diagnostic model appropriate for their host.

## Recommended downstream pattern

A static-analysis host should keep cpre focused on conditional reasoning:

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

1. Let the host own file reading, build-system discovery, and policy.
2. Call `cpre.analyze_source()` once per source/configuration being analyzed.
3. Catch `CpreError` for supported cpre failures.
4. Check `result.complete` before consuming findings.
5. Map `FindingKind` through structured values, not messages.
6. Preserve `depends_on_assumptions` when configuration-specific reasoning is enabled.
7. Apply only explicit `SuggestedEdit` objects according to host policy.
8. Import supported symbols from `cpre`, not implementation modules.

For a host that instead needs one selected translation-unit representation, use the [preprocessing integration checklist](preprocessing.md#downstream-integration-checklist).

## Choosing an interchange surface

The CLI's JSON output is a structural conditional-tree report. It should not be treated as a substitute for the Python object model.

Use:

- the Python API when both components run in Python and need the richest structured contract;
- `expression_to_dict()` / `expression_from_dict()` for stable symbolic-expression interchange;
- [SARIF](sarif.md) when findings need to cross a process/tool boundary;
- CLI text for human-facing terminal workflows;
- [concrete preprocessing](preprocessing.md) when a downstream parser/analyzer needs selected source and provenance.

## Compatibility expectations

cpre is in Beta. The documented top-level API is intended for real downstream integrations, and compatibility-sensitive changes should be deliberate and documented.

Evergreen integration code should depend on documented types, enum values, result fields, symbolic expression categories/tagged interchange, and `cpre.__all__`, not private helpers or implementation-specific ROBDD/parser details.

For the broader transformation and downstream-compatibility boundary, see [Downstream compatibility](downstream-compatibility.md).
