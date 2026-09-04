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

Or invoke the module directly:

```bash
python -m cpre.cpre source.c
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

The analyzer implementation currently lives in `cpre/cpre.py` and can be imported as:

```python
from cpre import cpre

tree = cpre.analyze_source(source_text)
```

The initial package layout intentionally preserves the analyzer implementation while establishing a reusable Python package boundary. Public API refinement can follow independently.

## Development

Run the test suite with:

```bash
pytest
```

The CI workflow runs the tests across supported Python versions on Linux, macOS, and Windows.

## Versioning

The package version is defined once in `cpre/__init__.py` as `__version__` and is consumed by `pyproject.toml` during builds. The initial version is `0.1.0`.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
