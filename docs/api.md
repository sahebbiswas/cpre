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
