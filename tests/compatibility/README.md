# Downstream preprocessing compatibility corpus

This corpus measures whether `cpre.preprocess_source` is suitable for static-analysis and AST consumers. The fixtures model preprocessing shapes used by C-GULL plus representative general macro behavior: macro-expanded `offsetof`/container recovery, nested conditionals, source-order macro state, multiline definitions, nested function-like macros, `#`/`##` operators, and externally configured branch selection.

Each fixture has two compatibility roles:

- `tests/test_downstream_compatibility.py` classifies cpre behavior as `supported`, `unsupported`, or `incomplete` and checks cpre's atomic-output/source-map contract.
- `tests/test_pcpp_differential.py` compares every supported fixture/configuration with `pcpp`. Any corpus fixture omitted from the equivalence gate must appear in `KNOWN_DIFFERENCES` with a reviewable reason.

For supported differential cases the gate compares macro-expanded preprocessing tokens, physical token line positions with pcpp line directives disabled, parseability through `pycparser`, and deterministic repeated output. The comparison deliberately normalizes comments, whitespace, and line-marker formatting; semantic token differences are never normalized away.

## Adding a fixture

1. Add a small, self-contained `.c` file under `tests/compatibility/fixtures/`. Keep it representative of a downstream preprocessing requirement rather than a synthetic tokenizer-only case.
2. Add or update its cpre classification in `tests/test_downstream_compatibility.py`. For supported cases that expand macros in executable/declaration text, list the physical invocation lines in `expanded_lines` when source-map coverage is meaningful.
3. Add every supported fixture/configuration to `SUPPORTED_CASES` in `tests/test_pcpp_differential.py`. If cpre intentionally cannot preprocess it, add the fixture to `KNOWN_DIFFERENCES` with the specific capability gap instead. The manifest test fails if a fixture is in neither set.
4. If the case is supported, keep it parseable by `pycparser` after preprocessing. Add local typedefs or declarations instead of relying on system headers, because include processing is intentionally tracked as a compatibility gap.
5. If the case is unsupported or incomplete, assert the exact `ErrorCode` and physical line so the gap remains measurable and cannot silently regress into partial output.
6. Prefer adding a focused assertion when the fixture represents a particularly important downstream shape, such as the `offsetof`/container recovery case.

`pcpp` and `pycparser` are development/test dependencies only; neither is required by the `cpre` runtime package.
