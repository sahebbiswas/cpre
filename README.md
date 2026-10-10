# cpre

[![CI](https://github.com/sahebbiswas/cpre/actions/workflows/ci.yml/badge.svg)](https://github.com/sahebbiswas/cpre/actions/workflows/ci.yml)

`cpre` analyzes Boolean conditions in C and C++ preprocessor conditional blocks without parsing the surrounding translation unit. It finds dead and redundant branches, simplifies conditions with bounded ROBDD reasoning, and also exposes a concrete preprocessing API for downstream analyzers that need one selected, macro-expanded source representation.

**Project status: Beta.** The documented CLI and top-level Python API are intended for downstream integration, while broader real-world use may still uncover compatibility, modeling, or performance edges before 1.0.

## What cpre provides

cpre has grown beyond a conditional-linting CLI into a set of focused, integration-oriented C/C++ preprocessing primitives. The surfaces are deliberately separate so callers can choose the level of interpretation they need:

- **Symbolic conditional analysis** — finds dead and redundant branches and distinguishes exact from contextual simplifications using bounded, deterministic ROBDD-backed reasoning. Known macro assumptions can constrain proofs without silently turning unknown macros into false.
- **Exact Boolean proofs and witnesses** — public APIs support satisfiability, implication, equivalence, exact simplification, and deterministic witness assignments, with explicit incomplete results when proof budgets are exhausted.
- **Lossless conditional structure** — `parse_conditionals()` exposes source-preserving blocks, directives, tokens, physical ranges, recovery diagnostics, and C23 `#elifdef`/`#elifndef` structure for refactoring and source-aware tools.
- **Macro Boolean simplification and rewriting** — analyzes object-like `#define` replacements with bounded ROBDD proofs, supporting both ordinary C truth semantics and opt-in symbolic-literal mode (`--symbolic-literal 0` / `--symbolic-zero`). Dedicated `simplify-macros` / `analyze-macros` CLI commands and Python APIs (`simplify_macros()`, `rewrite_macros()`) report and safely apply proven-equivalent simplifications in-place.
- **Concrete preprocessing** — `preprocess_source()` selects one configuration, tracks source-order macro state, performs bounded macro expansion (including variadics, stringification, token pasting, and `__VA_OPT__`), and preserves physical provenance through source mappings. External state is explicit through `MacroConfiguration`, deterministic preprocessing context, `__has_include` queries, and host-owned pragma handling.
- **Branch-covering configurations** — `cover_branches()` generates a small, deterministic set of concrete macro configurations that together select every reachable conditional-branch outcome, verifies each one with concrete preprocessing, and reports unreachable or uncovered outcomes with reasons.
- **Conservative integration contracts** — structured errors and diagnostics, deterministic ordering, explicit complete, partial and incomplete results, SARIF 2.1.0 findings, and downstream compatibility tests make it practical to embed cpre in analyzers and CI without parsing human-readable output or guessing about partial results.

The [documentation index](https://github.com/sahebbiswas/cpre/blob/main/docs/README.md) keeps the detailed contracts separate from this overview; the README is intentionally focused on the capabilities a new user should understand first.

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

## CLI quick start

Analyze one source file (evaluates both conditional directives and macro simplifications in a single pass):

```bash
cpre source.c
```

Preserve transient disabled controls with symbolic-zero semantics:

```bash
cpre --symbolic-zero source.c
```

Scan a directory recursively and emit SARIF:

```bash
cpre --recursive --sarif src > cpre.sarif
```

Fail CI when dead, redundant, or simplifiable branches/macros are found:

```bash
cpre --recursive --fail-on-findings src
```

The module form is equivalent:

```bash
python -m cpre source.c
```

Inspect simplified macro definitions or rewrite them safely in-place:

```bash
# Report-only mode (never modifies source)
cpre simplify-macros source.c

# Explicit in-place rewrite mode
cpre simplify-macros --rewrite source.c

# Preview the rewrite as a unified diff, or fail CI when a rewrite is available
cpre simplify-macros --diff source.c
cpre simplify-macros --check source.c
```

Select a concrete configuration and emit preprocessed source:

```bash
cpre preprocess source.c -D FEATURE
cpre preprocess source.c --compact
cpre preprocess source.c --list-unknown-macros   # macros the conditionals still need
```

Quickly evaluate and simplify arbitrary Boolean expressions:

```bash
cpre test-input 'A && (A || B)'
cpre test-input --json '(A && B) || (A && !B)'
```

By default, text and JSON reports show notable branches and simplified macros; `--verbose` includes unchanged branches and unsimplified macros. `--json` emits structural conditional trees alongside macro simplification results, while `--sarif` emits findings for static-analysis interchange. Specific analyses can be disabled with `--no-macros` or `--no-conditionals`. See the [CLI guide](https://github.com/sahebbiswas/cpre/blob/main/docs/cli.md) for discovery rules, batch output, stderr behavior, and the `0`/`1`/`2` exit-status contract.

## Python analysis quick start

```python
import cpre

try:
    result = cpre.analyze_source(source_text, filename="example.c")
except cpre.CpreError as error:
    print(error.code, error.location, error.message)
else:
    for finding in result.findings:  # always proven, even when the result is partial
        print(finding.kind, finding.location, finding.reason)
    for diagnostic in result.incomplete:
        print("analysis incomplete:", diagnostic)
```

Always check `result.complete` before treating an empty finding set as clean. Bounded analysis never exposes findings derived from partial proofs: when some independent conditionals exceed a resource limit, `result.partial` is true, the findings cover the rest of the file, and `result.incomplete_components` describes what was not analyzed.

See the [Python API integration guide](https://github.com/sahebbiswas/cpre/blob/main/docs/api.md) for findings, simplifications, edits, assumptions, resource limits, structured errors, and compatibility expectations.

## Concrete preprocessing quick start

`preprocess_source()` selects one configuration and returns a canonical coordinate/source-map-oriented representation:

```python
import cpre

result = cpre.preprocess_source(
    source_text,
    filename="example.c",
    assumptions={"FEATURE": True},
)

if result.complete:
    canonical = result.source
```

Canonical output remains the default. If a caller explicitly wants a presentation-oriented compact view, it can opt in separately:

```python
if result.complete:
    compact_source = cpre.compact(result)
```

`compact()` never runs automatically, and the canonical `result.source_map` describes `result.source`, not the compact view.

See [Concrete preprocessing](https://github.com/sahebbiswas/cpre/blob/main/docs/preprocessing.md) for macro configuration/state, mappings, removed-line provenance, deterministic predefined context, `__has_include`, pragma handling, incomplete results, and downstream-parser guidance.

## Documentation

The [documentation index](https://github.com/sahebbiswas/cpre/blob/main/docs/README.md) links the full user and integration guides. Key references include:

- [Command-line interface](https://github.com/sahebbiswas/cpre/blob/main/docs/cli.md)
- [Python API integration](https://github.com/sahebbiswas/cpre/blob/main/docs/api.md)
- [Macro Boolean simplification](https://github.com/sahebbiswas/cpre/blob/main/docs/macro-simplification.md)
- [Concrete preprocessing](https://github.com/sahebbiswas/cpre/blob/main/docs/preprocessing.md)
- [Branch-covering configurations](https://github.com/sahebbiswas/cpre/blob/main/docs/branch-coverage.md)
- [Compact preprocessing output](https://github.com/sahebbiswas/cpre/blob/main/docs/compact-preprocessing.md)
- [Macro expansion](https://github.com/sahebbiswas/cpre/blob/main/docs/macro-expansion.md)
- [Concrete macro configuration](https://github.com/sahebbiswas/cpre/blob/main/docs/concrete-configuration.md)
- [Concrete conditional expressions](https://github.com/sahebbiswas/cpre/blob/main/docs/conditional-expressions.md)
- [Host-assisted `__has_include`](https://github.com/sahebbiswas/cpre/blob/main/docs/has-include.md)
- [Pragma handling](https://github.com/sahebbiswas/cpre/blob/main/docs/pragma-handling.md)
- [SARIF output](https://github.com/sahebbiswas/cpre/blob/main/docs/sarif.md)
- [Downstream compatibility](https://github.com/sahebbiswas/cpre/blob/main/docs/downstream-compatibility.md)
- [pcpp replacement readiness](https://github.com/sahebbiswas/cpre/blob/main/docs/pcpp-readiness.md)

The README uses absolute repository links so the same navigation works when rendered as the package long description on PyPI.

## Development and testing

Install the development dependencies and run the suite:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Release validation also builds both distributions and checks their metadata/long description:

```bash
python -m build
python -m twine check dist/*
```

GitHub Actions runs tests across supported Python versions on Linux, macOS, and Windows, plus a build/twine/public-wheel-contract job. The release workflow tests the built wheel and sdist, uploads to TestPyPI and checks that install before publishing to PyPI; see [CONTRIBUTING](https://github.com/sahebbiswas/cpre/blob/main/CONTRIBUTING.md#releasing).

When extending cpre, keep downstream integrations on the documented top-level `cpre` API. Preserve deterministic ordering, structured diagnostics, and complete/incomplete semantics rather than requiring consumers to parse human-readable output.

## Versioning

`cpre` follows [Semantic Versioning](https://semver.org/). The package version is defined once by `cpre.__version__` and consumed by `pyproject.toml` during builds. Every pull request merged to `main` bumps it by one step (patch for fixes, docs and tooling; minor for new features and, while `cpre` is `0.x`, deliberate compatibility breaks), and CI checks the bump. Releases are cut from `main` when it is stable, so not every version is published to PyPI. See [CONTRIBUTING](https://github.com/sahebbiswas/cpre/blob/main/CONTRIBUTING.md#versioning) for the full rules.

`0.7.0` marked the transition from Alpha to Beta. During Beta, the documented top-level API is intended for real integrations; compatibility-sensitive changes must be deliberate and called out as a **Behaviour change:** in the pull request and release notes.

## License

Licensed under the Apache License, Version 2.0. See the [repository license](https://github.com/sahebbiswas/cpre/blob/main/LICENSE).
