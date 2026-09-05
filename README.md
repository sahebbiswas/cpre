# cpre

`cpre` analyzes Boolean conditions in C and C++ preprocessor conditional blocks without preprocessing or parsing the surrounding source code. It identifies dead and redundant branches, simplifies Boolean conditions using ROBDD-backed reasoning, and preserves value-bearing expressions as opaque Boolean predicates.

## Installation

```bash
python -m pip install cpre
```

For development:

```bash
git clone https://github.com/sahebbiswas/cpre.git
cd cpre
python -m pip install -e ".[dev]"
```

## Command-line usage

Analyze a single source file:

```bash
cpre source.c
```

You can also invoke the package directly:

```bash
python -m cpre source.c
```

On Windows, the Python launcher works as well:

```bash
py -3 -m cpre source.c
```

Useful options include:

```text
--recursive          recursively scan C/C++ source files under directories
--json               emit JSON output
--verbose            include unchanged conditional branches
--fail-on-findings   exit with status 1 when dead or redundant branches are found
```

By default, text and JSON reports are filtered to branches that are dead, redundant, or whose condition can be simplified. `--verbose` restores the full conditional tree.

Examples:

```bash
cpre --recursive path/to/project
cpre --recursive --json path/to/project
cpre --fail-on-findings source.c
```

## Python API

`cpre` exposes a stable top-level API for programmatic consumers:

```python
import cpre

try:
    result = cpre.analyze_source(source_text, filename="example.c")
except cpre.CpreError as error:
    line = error.location.line if error.location else 1
    print(error.code, line, error.message)
else:
    for finding in result.findings:
        if finding.exact_simplification is not None:
            print("safe exact replacement:", finding.exact_simplification.replacement)
        if finding.contextual_simplification is not None:
            print("context-only replacement:", finding.contextual_simplification.replacement)
```

The supported public symbols are:

- `analyze_source`
- `AnalysisResult`
- `CpreError`
- `ParseError`
- `AnalysisError`
- `ErrorCode`
- `ConditionError` (compatibility alias for `CpreError`)
- `Finding`
- `FindingKind`
- `FixConfidence`
- `SourceLocation`
- `SourceRange`
- `SuggestedEdit`
- `ExactSimplification`
- `ContextualSimplification`
- `MacroAssumptions`
- `ConditionalTree`
- `__version__`

`analyze_source` returns an `AnalysisResult` containing the analyzed conditional tree and an ordered tuple of structured findings. Finding kinds distinguish dead branches, redundant branches, exactly simplifiable conditions, and contextual simplifications.

### Configuration-aware macro assumptions

The default analysis remains fully symbolic and configuration-independent. Callers that already know part of a translation unit's preprocessor state may opt into configuration-aware reasoning by passing `assumptions`.

For simple Boolean macro values, a mapping is sufficient:

```python
result = cpre.analyze_source(
    source_text,
    assumptions={
        "FEATURE_A": True,
        "FEATURE_B": False,
    },
)
```

A mapping constrains the Boolean truth of bare macro identifiers. It does not by itself claim that a false-valued macro is undefined.

Use `MacroAssumptions` when definedness must be modeled independently:

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

This distinction matters because a macro may be defined while evaluating to zero. Under configuration-aware analysis:

- `#ifdef X`, `#ifndef X`, `#elifdef X`, `#elifndef X`, and `defined(X)` use the known defined/undefined state when supplied.
- Bare Boolean `X` uses a supplied Boolean value when one is known.
- A bare macro known true necessarily implies that the macro is defined.
- A macro known undefined necessarily cannot evaluate true.
- Unmentioned macros remain symbolic; cpre never silently treats unknown macros as false.
- Integer/value-bearing macro evaluation is not inferred beyond explicit Boolean values in this API.

Contradictory assumptions raise `AnalysisError` with `ErrorCode.INVALID_ASSUMPTIONS`. For example, one macro cannot be both listed in `defined` and `undefined`, and an explicitly undefined macro cannot simultaneously have Boolean value `True`.

Each `Finding` contains `depends_on_assumptions`. It is `True` only when the supplied assumptions materially changed the branch status or simplification proof that produced that finding. Downstream analyzers can therefore distinguish configuration-specific diagnostics from findings that were already valid under fully symbolic analysis.

An `ExactSimplification` is globally equivalent when no assumptions are supplied. When assumptions participate in the proof, it is exact for every assignment consistent with that assumption set. `Finding.depends_on_assumptions` tells consumers when that narrower guarantee applies.

### Structured errors and diagnostics

Malformed conditional input raises `ParseError`, rooted in the public `CpreError` hierarchy. Consumers should use structured fields rather than parsing exception strings:

- `error.code` is a stable `ErrorCode` such as `EXPRESSION_SYNTAX`, `UNMATCHED_DIRECTIVE`, `MISPLACED_DIRECTIVE`, `MALFORMED_MACRO_DIRECTIVE`, `TRAILING_DIRECTIVE_TEXT`, `UNTERMINATED_CONDITIONAL`, or `INVALID_ASSUMPTIONS`.
- `error.location` is a `SourceLocation` containing the physical one-based line and, when available, column. Backslash-continued directives retain the physical line and column where the failure occurred.
- `error.message` is human-readable diagnostic text.
- `error.filename` carries the optional `filename` value passed to `analyze_source` without requiring it to be recovered from rendered text.

`AnalysisError` represents supported analysis/API failures distinct from malformed source. Unexpected programming errors are not converted into `CpreError`.

The public wrapper preserves the original parser exception as `__cause__` when translating malformed source. Library calls do not print, call `sys.exit()`, or otherwise perform CLI output. The `cpre.cli` layer is responsible for rendering diagnostics and mapping failures to terminal exit codes.

### Simplification guarantees

The public API deliberately represents the two simplification modes with different types:

- `ExactSimplification` is globally Boolean-equivalent to the original source condition under the active analysis assumptions. With no assumptions, this means every assignment of the modeled predicates. With explicit assumptions, it means every assignment consistent with those assumptions.
- `ContextualSimplification` is equivalent only within the branch's effective reachable context. That context includes enclosing conditional branches, exclusions introduced by preceding `#if`/`#elif` branches, and any explicit macro assumptions.

A simplification field is `None` when that mode does not produce a semantic improvement. Formatting-only normalization and results equivalent to the current comparison form are represented by absence rather than by returning the original expression again. Contextual simplification is compared with the exact form when one exists, so it is present only when branch context provides an additional simplification.

Boolean constants are represented canonically as the strings `"0"` and `"1"`. Contextual `0` is consistent with an unreachable/dead condition in its effective context, while contextual `1` supports redundant-branch classification.

For compatibility with the initial `0.2` API, `Finding.simplified_condition` and `Finding.contextual_condition` remain read-only convenience properties that return the corresponding replacement string or `None`. New integrations should prefer the typed `exact_simplification` and `contextual_simplification` fields because their equivalence guarantees are explicit at the type level.

When a direct source replacement is safe to represent, `Finding.edit` contains a `SuggestedEdit` with a physical `SourceRange`, replacement text, and `FixConfidence`. Dead/redundant branch findings deliberately do not imply branch deletion, and macro directive forms that cannot be mechanically replaced have no edit.

Consumers should import supported symbols from `cpre` rather than from `cpre.cpre` or private helpers such as the ROBDD implementation. Private implementation details are not part of the compatibility contract.

The optional `filename` argument is metadata for downstream tools and does not affect analysis semantics.

## Analysis behavior

Without assumptions, `cpre` reasons symbolically about preprocessor conditions rather than evaluating one specific build configuration. Identifiers are treated as Boolean flags, while value-bearing expressions outside the Boolean model are preserved as opaque predicates.

The analyzer can identify:

- dead or unreachable conditional branches
- redundant conditions that are always true in their effective branch context
- exact Boolean simplifications
- context-dependent simplifications derived from enclosing and preceding branch conditions
- configuration-specific versions of the same findings when explicit macro assumptions are supplied

The engine uses ROBDD-backed Boolean reasoning for exact satisfiability and equivalence checks while retaining a dependency-free runtime. ROBDD nodes and implementation details are intentionally private and are not exposed by the public API.

## Testing

Install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the complete test suite with:

```bash
pytest
```

The tests include parser and analyzer unit coverage, CLI behavior, property-based checks, source-location handling, filtered/verbose reporting, structured diagnostics, macro-assumption behavior, and the stable downstream API contract.

The GitHub Actions CI matrix runs the suite across supported Python versions on Linux, macOS, and Windows. Changes should keep both CLI behavior and public API tests green unless the corresponding behavior is intentionally revised.

## Extending cpre

The implementation is split into focused internal modules for the data model, expression handling, ROBDD reasoning, directive parsing, analysis, reporting, and source discovery. `cpre/api.py` provides the stable programmatic boundary, `cpre/cli.py` owns terminal behavior, and `cpre/cpre.py` remains a compatibility facade for historical internal callers.

When adding or changing analyzer behavior:

1. Keep CLI-specific argument parsing, output formatting, and exit-code handling separate from programmatic APIs.
2. Prefer adding structured data to the public API rather than requiring consumers to parse human-readable output.
3. Avoid exposing ROBDD node IDs, parser internals, or private helpers through the top-level package.
4. Add focused regression tests for new Boolean identities, directives, source-location cases, finding classifications, assumption semantics, and diagnostic codes.
5. Preserve deterministic results and ordering so static-analysis clients can rely on repeatable output.
6. Treat changes to top-level public imports and result semantics as compatibility-sensitive.

Planned extension areas include bounded analysis resources for large or pathological inputs and future value-aware macro predicates where they can be modeled safely.

## Versioning

The package version is defined in `cpre/__init__.py` as `__version__` and is consumed by `pyproject.toml` during builds.

Public API additions use a minor version bump, while backward-compatible fixes use patch releases. Changes that affect the documented public API should update compatibility tests alongside the implementation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
