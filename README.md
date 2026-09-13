# cpre

`cpre` analyzes Boolean conditions in C and C++ preprocessor conditional blocks without parsing the surrounding translation unit. It finds dead and redundant branches, simplifies conditions with bounded ROBDD reasoning, and also exposes a concrete preprocessing API for downstream analyzers that need one selected, macro-expanded source representation.

**Project status: Beta.** The documented CLI and top-level Python API are intended for downstream integration, while broader real-world use may still uncover compatibility, modeling, or performance edges before 1.0.

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

Analyze one source file:

```bash
cpre source.c
```

Scan a directory recursively and emit SARIF:

```bash
cpre --recursive --sarif src > cpre.sarif
```

Fail CI when dead or redundant branches are found:

```bash
cpre --recursive --fail-on-findings src
```

The module form is equivalent:

```bash
python -m cpre source.c
```

By default, text and JSON reports show notable branches only; `--verbose` includes unchanged branches. `--json` emits the structural conditional tree, while `--sarif` emits findings for static-analysis interchange. See the [CLI guide](https://github.com/sahebbiswas/cpre/blob/main/docs/cli.md) for discovery rules, batch output, stderr behavior, and the `0`/`1`/`2` exit-status contract.

## Python analysis quick start

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

Always check `result.complete` before treating an empty finding set as clean. Bounded analysis never exposes findings derived from partial proofs.

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
- [Concrete preprocessing](https://github.com/sahebbiswas/cpre/blob/main/docs/preprocessing.md)
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

GitHub Actions runs tests across supported Python versions on Linux, macOS, and Windows, plus a build/twine/public-wheel-contract job.

When extending cpre, keep downstream integrations on the documented top-level `cpre` API. Preserve deterministic ordering, structured diagnostics, and complete/incomplete semantics rather than requiring consumers to parse human-readable output.

## Versioning

The package version is defined by `cpre.__version__` and consumed by `pyproject.toml` during builds.

`0.7.0` marked the transition from Alpha to Beta. During Beta, the documented top-level API is intended for real integrations; compatibility-sensitive changes should be deliberate and documented. Incremental features and fixes generally use patch releases, while deliberate compatibility changes should be reflected more prominently in release planning.

## License

Licensed under the Apache License, Version 2.0. See the [repository license](https://github.com/sahebbiswas/cpre/blob/main/LICENSE).
