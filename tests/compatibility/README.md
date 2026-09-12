# Downstream preprocessing compatibility corpus

This corpus measures whether `cpre.preprocess_source` is suitable for static-analysis and AST consumers. The initial fixtures model preprocessing shapes used by C-GULL: macro-expanded `offsetof`/container recovery, nested conditionals, source-order macro state, multiline definitions, and nested function-like macros.

Each fixture is classified in `tests/test_downstream_compatibility.py` as one of:

- `supported`: preprocessing must complete deterministically, preserve source mappings, and produce C accepted by `pycparser`.
- `unsupported`: preprocessing must fail atomically with a specific structured error code and physical source line.
- `incomplete`: the source is valid but cannot be concretely selected under the available macro state; the expected incomplete diagnostic and line are asserted.

## Adding a fixture

1. Add a small, self-contained `.c` file under `tests/compatibility/fixtures/`. Keep it representative of a downstream preprocessing requirement rather than a synthetic tokenizer-only case.
2. Add a `CompatibilityCase` entry with its expected classification. For supported cases that expand macros in executable/declaration text, list the physical invocation lines in `expanded_lines`.
3. If the case is supported, keep it parseable by `pycparser` after preprocessing. Add local typedefs or declarations instead of relying on system headers, because include processing is intentionally tracked as a compatibility gap.
4. If the case is unsupported or incomplete, assert the exact `ErrorCode` and physical line so the gap remains measurable and cannot silently regress into partial output.
5. Prefer adding a focused assertion when the fixture represents a particularly important downstream shape, such as the `offsetof`/container recovery case.

`pycparser` is a development/test dependency only; it is not required by the `cpre` runtime package.
