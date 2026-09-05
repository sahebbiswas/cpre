# cpre

`cpre` analyzes Boolean conditions in C and C++ preprocessor conditional blocks without preprocessing or parsing the surrounding source code. It identifies dead and redundant branches, simplifies Boolean conditions using ROBDD-backed reasoning, and preserves value-bearing expressions as opaque Boolean predicates.

**Project status: Beta.** The CLI and documented Python API are suitable for downstream integration, while broader real-world use may still uncover modeling, compatibility, or performance edge cases before a 1.0 release.

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

## Documentation

- [Python API integration guide](docs/api.md) — developer-focused guidance for embedding cpre in tools, linters, and scripts.
- [SARIF output](docs/sarif.md) — SARIF 2.1.0 format, rule mapping, fixes, and code-scanning integration.

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
--json               emit the structural conditional tree as JSON
--sarif              emit findings as SARIF 2.1.0
--verbose            include unchanged conditional branches in text/JSON reports
--fail-on-findings   exit with status 1 when dead or redundant branches are found
```

`--json` and `--sarif` are mutually exclusive. JSON is the structural conditional-tree representation; SARIF is the interoperable findings format for static-analysis integrations.

By default, text and JSON reports are filtered to branches that are dead, redundant, or whose condition can be simplified. `--verbose` restores the full conditional tree.

Examples:

```bash
cpre --recursive path/to/project
cpre --recursive --json path/to/project
cpre --recursive --sarif path/to/project > cpre.sarif
cpre --fail-on-findings source.c
```

See [SARIF output](docs/sarif.md) for rule IDs, fixes, failure notifications, and GitHub code-scanning usage.

## Python API

`cpre` exposes a stable top-level API for programmatic consumers:

```python
import cpre

try:
    result = cpre.analyze_source(source_text, filename="example.c")
except cpre.CpreError as error:
    print(error.code, error.location, error.message)
else:
    if not result.complete:
        for diagnostic in result.incomplete:
            print("analysis incomplete:", diagnostic)
    else:
        for finding in result.findings:
            print(finding.kind, finding.location, finding.reason)
```

Downstream tools should check `result.complete` before treating an empty findings tuple as a clean analysis. Bounded ROBDD exhaustion returns a structured incomplete result and never exposes findings derived from partial proofs.

The supported API includes structured findings, exact and contextual simplifications, source edits, macro assumptions, deterministic analysis limits, and stable error codes. See the [Python API integration guide](docs/api.md) for the full integration contract and examples.

Consumers should import supported symbols from `cpre` rather than internal modules such as `cpre.robdd`, `cpre.parser`, or compatibility-facade internals.

### Configuration-aware macro assumptions

The default analysis is symbolic and configuration-independent. Callers that know part of a translation unit's preprocessor state can provide assumptions:

```python
result = cpre.analyze_source(
    source_text,
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
    values={"FEATURE_A": True, "FEATURE_ZERO": False},
)
result = cpre.analyze_source(source_text, assumptions=assumptions)
```

Unmentioned macros remain symbolic; cpre never silently treats unknown macros as false. Each finding records whether its proof materially depends on supplied assumptions.

### Simplification guarantees

The public API distinguishes two simplification guarantees:

- `ExactSimplification` is Boolean-equivalent to the original condition under the active assumptions.
- `ContextualSimplification` is equivalent only within the branch's effective reachable context.

When a direct replacement is safe to represent, `Finding.edit` contains a `SuggestedEdit` with a physical source range, replacement text, and confidence. Dead/redundant branch findings deliberately do not imply branch deletion.

### Structured errors and bounded analysis

Malformed source and invalid API input use the public `CpreError` hierarchy with stable `ErrorCode` values, locations, messages, and optional filenames. Consumers should use these fields rather than parsing rendered error strings.

ROBDD reasoning is deterministically bounded by `AnalysisOptions` (`max_atoms`, `max_bdd_nodes`, and `max_work`). If a limit is exceeded, `AnalysisResult.complete` is false and `AnalysisResult.incomplete` explains the resource, configured limit, observed usage, and source location when available.

See [Python API integration guide](docs/api.md) for recommended handling of errors, incomplete analysis, assumptions, and edits.

## Analysis behavior

Without assumptions, `cpre` reasons symbolically about preprocessor conditions rather than evaluating one specific build configuration. Identifiers are treated as Boolean flags, while value-bearing expressions outside the Boolean model are preserved as opaque predicates.

The analyzer can identify:

- dead or unreachable conditional branches
- redundant conditions that are always true in their effective branch context
- exact Boolean simplifications
- context-dependent simplifications derived from enclosing and preceding branch conditions
- configuration-specific findings when explicit macro assumptions are supplied

The engine uses ROBDD-backed Boolean reasoning for exact satisfiability and equivalence checks while retaining a dependency-free runtime. ROBDD nodes and implementation details are intentionally private.

## Testing

Install development dependencies and run the test suite:

```bash
python -m pip install -e ".[dev]"
pytest
```

The GitHub Actions CI matrix runs the suite across supported Python versions on Linux, macOS, and Windows.

## Extending cpre

The implementation is split into focused internal modules for the data model, expression handling, ROBDD reasoning, directive parsing, analysis, reporting, SARIF rendering, and source discovery. `cpre/api.py` provides the stable programmatic boundary and `cpre/cli.py` owns terminal behavior.

When adding analyzer behavior, preserve structured public data, deterministic ordering, complete/incomplete semantics, and compatibility of top-level public imports. Prefer extending the public result model rather than requiring consumers to parse human-readable output.

## Versioning

The package version is defined in `cpre/__init__.py` as `__version__` and is consumed by `pyproject.toml` during builds.

`0.7.0` marks cpre's transition from Alpha to Beta. During the Beta series, the documented top-level API is intended for real downstream integrations and compatibility-sensitive changes should be deliberate and documented. The path to 1.0 will emphasize downstream integration experience and validation against larger real-world C/C++ codebases.

Public API additions use a minor version bump, while backward-compatible fixes use patch releases. Changes that affect documented public behavior should update compatibility tests alongside the implementation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
