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

## Usage

After installation, run:

```bash
cpre source.c
```

Or invoke the package directly:

```bash
python -m cpre source.c
```

Useful options include:

```text
--recursive          recursively scan C/C++ source files under directories
--json               emit JSON output
--verbose            include unchanged conditional branches
--fail-on-findings   exit with status 1 when dead or redundant branches are found
```

By default, text and JSON reports are filtered to branches that are dead, redundant, or whose condition can be simplified. `--verbose` restores the full conditional tree.

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
        print(finding.kind, finding.location.line, finding.reason)
```

The supported public symbols are:

- `analyze_source`
- `AnalysisResult`
- `ConditionError`
- `Finding`
- `FindingKind`
- `SourceLocation`
- `ConditionalTree`
- `__version__`

`analyze_source` returns an `AnalysisResult` containing the analyzed conditional tree and an ordered tuple of structured findings. Finding kinds currently distinguish dead branches, redundant branches, globally simplifiable conditions, and contextual simplifications. Malformed conditional input raises `ConditionError`, which is the supported public catch-all for parser and directive-structure failures.

Consumers should import these symbols from `cpre` rather than from `cpre.cpre` or relying on private helpers such as the ROBDD implementation. Private implementation details are not part of the compatibility contract.

The optional `filename` argument is metadata for downstream tools and does not affect analysis semantics.

## Development

Run the test suite with:

```bash
pytest
```

The CI workflow runs the tests across supported Python versions on Linux, macOS, and Windows.

## Versioning

The package version is defined once in `cpre/__init__.py` as `__version__` and is consumed by `pyproject.toml` during builds. Public API additions are released with a minor version bump while backward-compatible fixes use patch releases.

## Publishing releases

Publishing to PyPI is handled by `.github/workflows/release.yml` using PyPI Trusted Publishing. The workflow runs whenever a GitHub release is published, builds both source and wheel distributions, and publishes them to PyPI without a stored API token.

Before the first release, configure a PyPI Trusted Publisher for this repository with:

- Owner: `sahebbiswas`
- Repository: `cpre`
- Workflow: `release.yml`
- Environment: `pypi`

Create a GitHub Actions environment named `pypi` as well. It can optionally require approval before deployment.

Before publishing a release, update `cpre/__init__.py` to the release version and create the GitHub release from a matching tag. Both plain and `v`-prefixed tags are accepted when they match the package version; a mismatch causes the publish workflow to fail before uploading anything.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
