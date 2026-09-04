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
except cpre.ConditionError as error:
    print(f"invalid preprocessor condition: {error}")
else:
    for finding in result.findings:
        if finding.exact_simplification is not None:
            print("safe global replacement:", finding.exact_simplification.replacement)
        if finding.contextual_simplification is not None:
            print("context-only replacement:", finding.contextual_simplification.replacement)
```

The supported public symbols are:

- `analyze_source`
- `AnalysisResult`
- `ConditionError`
- `Finding`
- `FindingKind`
- `SourceLocation`
- `ExactSimplification`
- `ContextualSimplification`
- `ConditionalTree`
- `__version__`

`analyze_source` returns an `AnalysisResult` containing the analyzed conditional tree and an ordered tuple of structured findings. Finding kinds currently distinguish dead branches, redundant branches, globally simplifiable conditions, and contextual simplifications.

### Simplification guarantees

The public API deliberately represents the two simplification modes with different types:

- `ExactSimplification` is globally Boolean-equivalent to the original source condition for every assignment of the modeled predicates. Its `replacement` is suitable for consumers that require a context-independent mechanical replacement.
- `ContextualSimplification` is equivalent only within the branch's effective reachable context. That context includes enclosing conditional branches and exclusions introduced by preceding `#if`/`#elif` branches. A contextual replacement must not be treated as globally equivalent to the source condition.

A simplification field is `None` when that mode does not produce a semantic improvement. In particular, formatting-only normalization and results equivalent to the current comparison form are represented by absence rather than by returning the original expression again. Contextual simplification is compared with the exact form when one exists, so it is present only when branch context provides an additional simplification.

Boolean constants are represented canonically as the strings `"0"` and `"1"`. Contextual `0` is consistent with an unreachable/dead condition in its effective context, while contextual `1` supports redundant-branch classification.

For compatibility with the initial `0.2` API, `Finding.simplified_condition` and `Finding.contextual_condition` remain read-only convenience properties that return the corresponding replacement string or `None`. New integrations should prefer the typed `exact_simplification` and `contextual_simplification` fields because their equivalence guarantees are explicit at the type level.

Malformed conditional input raises `ConditionError`, which is the supported public catch-all for parser and directive-structure failures.

Consumers should import supported symbols from `cpre` rather than from `cpre.cpre` or private helpers such as the ROBDD implementation. Private implementation details are not part of the compatibility contract.

The optional `filename` argument is metadata for downstream tools and does not affect analysis semantics.

## Analysis behavior

`cpre` reasons symbolically about preprocessor conditions rather than evaluating one specific build configuration. Identifiers are treated as Boolean flags, while value-bearing expressions that are outside the Boolean model are preserved as opaque predicates.

The analyzer can identify:

- dead or unreachable conditional branches
- redundant conditions that are always true in their effective branch context
- globally equivalent Boolean simplifications
- context-dependent simplifications derived from enclosing and preceding branch conditions

The current engine uses ROBDD-backed Boolean reasoning for exact satisfiability and equivalence checks while retaining a dependency-free runtime. ROBDD nodes and implementation details are intentionally private and are not exposed by the public API.

## Testing

Install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the complete test suite with:

```bash
pytest
```

The tests include parser and analyzer unit coverage, CLI behavior, property-based checks, source-location handling, filtered/verbose reporting, and the stable public API contract.

The GitHub Actions CI matrix runs the suite across supported Python versions on Linux, macOS, and Windows. Changes should keep both CLI behavior and public API tests green unless the corresponding behavior is intentionally revised.

## Extending cpre

The initial implementation remains in `cpre/cpre.py`, while `cpre/api.py` provides the stable programmatic boundary for downstream consumers.

When adding or changing analyzer behavior:

1. Keep CLI-specific argument parsing, output formatting, and exit-code handling separate from programmatic APIs.
2. Prefer adding structured data to the public API rather than requiring consumers to parse human-readable output.
3. Avoid exposing ROBDD node IDs, parser internals, or private helpers through the top-level package.
4. Add focused regression tests for new Boolean identities, directives, source-location cases, and finding classifications.
5. Preserve deterministic results and ordering so static-analysis clients can rely on repeatable output.
6. Treat changes to top-level public imports and result semantics as compatibility-sensitive.

Planned extension areas include richer structured fix metadata, configuration-aware macro assumptions, structured diagnostic errors, and bounded analysis resources for large or pathological inputs.

## Versioning

The package version is defined in `cpre/__init__.py` as `__version__` and is consumed by `pyproject.toml` during builds.

Public API additions use a minor version bump, while backward-compatible fixes use patch releases. Changes that affect the documented public API should update compatibility tests alongside the implementation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
