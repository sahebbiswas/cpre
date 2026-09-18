# Downstream preprocessing compatibility corpus

This corpus measures whether `cpre.preprocess_source` is suitable for static-analysis and AST consumers. The fixtures model preprocessing shapes used by C-GULL plus representative general macro behavior: macro-expanded `offsetof`/container recovery, nested conditionals, source-order macro state, multiline definitions, nested function-like macros, `#`/`##` operators, and externally configured branch selection.

Each fixture has two compatibility roles:

- `tests/test_downstream_compatibility.py` classifies cpre behavior as `supported`, `unsupported`, or `incomplete` and checks cpre's atomic-output/source-map contract. Unsupported and incomplete cases must never expose partial source as if preprocessing succeeded.
- `tests/test_pcpp_differential.py` compares every supported fixture/configuration with `pcpp`. Any corpus fixture omitted from the equivalence gate must appear in `EXPLICIT_NONCOMPLETE_CASES` with a reviewable reason and a structured non-complete result.

For supported differential cases the gate compares macro-expanded preprocessing tokens, original token line positions resolved through pcpp line directives, parseability through `pycparser`, and deterministic repeated output. The comparison deliberately normalizes comments, whitespace, and line-marker formatting; semantic token differences are never normalized away.

The [readiness report](../../docs/pcpp-readiness.md) records the concrete-preprocessing verdict. A green differential suite includes regression assertions for known gaps and by itself does not mean `pcpp` can be removed; the versioned C-GULL profile supplies the reviewed downstream boundary and non-impact evidence.

## C-GULL concrete preprocessing migration profile

Issue #39 defines a narrower, reviewed boundary for intentionally unsupported constructs that are not required by the pinned C-GULL concrete-preprocessing migration corpus. The public rationale and downstream evidence are in [`docs/cgull-migration-profile.md`](../../docs/cgull-migration-profile.md); the machine-readable scope is `cgull-profile.json`.

`tests/test_cgull_migration_profile.py` enforces that concrete boundary:

- project headers are already expanded before cpre receives the prepared TU;
- remaining unresolved include directive lines are masked by the C-GULL adapter while physical line endings are preserved;
- C-GULL's typedef prelude is injected after cpre;
- raw reachable includes remain atomically unsupported by cpre.

The fixture under `cgull_profile/` is deliberately separate from the differential fixture directory. It models caller-prepared input rather than claiming that cpre itself resolved an include.

## C-GULL symbolic preprocessor migration gate

Issue #63 defines a separate compatibility contract for replacing C-GULL's local symbolic modules. It must not be inferred from the concrete `pcpp` profile above. The versioned readiness record is [`docs/cgull-symbolic-migration-readiness.md`](../../docs/cgull-symbolic-migration-readiness.md), and the machine-readable pin/inventory is `cgull-symbolic-profile.json`.

`tests/test_cgull_symbolic_migration_profile.py` uses top-level `cpre` imports only and covers:

- symbolic atom categories, normalization, deterministic formatting, and structured round-trip;
- lossless conditional structure, physical ranges, C23 forms, recovery diagnostics, continuations, comments, and literals;
- exact satisfiability, implication, equivalence, simplification, deterministic witnesses, and explicit limit exhaustion;
- representative C-GULL branch reachability/redundancy/contextual-simplification semantics;
- representative configuration witnesses, including the distinction between macro truth, macro definedness, and opaque predicates;
- explicit reviewed mappings for the small naming differences that remain downstream-owned.

The CI build job copies this test and its JSON profile outside the repository checkout and runs them against the built/installed wheel. This makes private source-tree imports or packaging omissions fail the compatibility gate.

## Adding a concrete preprocessing fixture

1. Add a small, self-contained `.c` file under `tests/compatibility/fixtures/`. Keep it representative of a downstream preprocessing requirement rather than a synthetic tokenizer-only case.
2. Add or update its cpre classification in `tests/test_downstream_compatibility.py`. For supported cases that expand macros in executable/declaration text, list the physical invocation lines in `expanded_lines` when source-map coverage is meaningful.
3. Add every supported fixture/configuration to `SUPPORTED_CASES` in `tests/test_pcpp_differential.py`. If cpre intentionally cannot preprocess it, add the fixture to `EXPLICIT_NONCOMPLETE_CASES` with the specific capability gap instead. The manifest test fails if a fixture is in neither set.
4. If the case is supported, keep it parseable by `pycparser` after preprocessing. Add local typedefs or declarations instead of relying on system headers, because include processing is outside cpre's supported boundary unless the caller has already prepared the TU according to an explicitly reviewed profile.
5. If the case is unsupported or incomplete, assert the exact `ErrorCode` and physical line so the gap remains measurable and cannot silently regress into partial output.
6. Prefer adding a focused assertion when the fixture represents a particularly important downstream shape, such as the `offsetof`/container recovery case.
7. If `pcpp` performs semantics cpre does not support, require an explicit non-complete cpre result and assert the oracle behavior separately when useful. Update the readiness report's counts, configurations and verdict when changing the corpus. Do not classify a case as supported merely because both outputs parse.

For symbolic compatibility changes, update the pinned inventory and focused semantic cases in `cgull-symbolic-profile.json` and `tests/test_cgull_symbolic_migration_profile.py`; never weaken atom-category, source-range, proof-completion, or accepted-difference assertions merely to keep the gate green.

`pcpp` and `pycparser` are development/test dependencies only; neither is required by the `cpre` runtime package.
