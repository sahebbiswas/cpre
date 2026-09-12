# C-GULL pcpp replacement readiness report

**Verdict: READY for the bounded C-GULL migration profile pinned below.**

This report closes the readiness gate tracked by [issue #28](https://github.com/sahebbiswas/cpre/issues/28). It is evidence that C-GULL may replace its current `pcpp` preprocessing tier with `cpre.preprocess_source` **when the reviewed prepared-translation-unit boundary is preserved**. It is not a claim of general GCC/Clang preprocessor compatibility.

The validated downstream reference is [C-GULL `c61b275`](https://github.com/sahebbiswas/cgull/tree/c61b27520624c074661afa0a615160944b31ab3d), which remained the C-GULL `main` revision during the final #28 validation on 2026-09-12. The migration profile is versioned in [`tests/compatibility/cgull-profile.json`](../tests/compatibility/cgull-profile.json) and documented in [`cgull-migration-profile.md`](cgull-migration-profile.md).

## Final gate result

All blockers discovered by the original pcpp differential gate are resolved or explicitly bounded with reviewed non-impact evidence:

| Follow-up | Final status | Evidence |
| --- | --- | --- |
| [#36: numeric conditions](https://github.com/sahebbiswas/cpre/issues/36) | Resolved | Bounded macro-expanded integer conditions are in the supported differential set. |
| [#37: configuration policy](https://github.com/sahebbiswas/cpre/issues/37) | Resolved | `MacroConfiguration` supplies concrete definitions and an opt-in closed unknown-name policy matching the migration needs. |
| [#38: built-ins/directives](https://github.com/sahebbiswas/cpre/issues/38) | Resolved | Unmodeled predefined macro value uses and unsupported reachable directives return structured atomic non-complete results; they are no longer silently certified as complete. |
| [#39: unsupported-input scope](https://github.com/sahebbiswas/cpre/issues/39) | Resolved by reviewed profile | Project headers are expanded before cpre; unresolved include lines are masked at the adapter boundary; the pinned C-GULL corpus has zero `__VA_OPT__` occurrences. |
| Final downstream validation | Satisfied for pinned profile | The pinned revision was still current C-GULL `main`; the `__VA_OPT__` inventory remained empty; the include-expansion/source-provenance boundary and current pcpp masking behavior remain the reviewed migration boundary. |

The machine-readable profile records `status: ready`, zero supported-case divergences, zero accepted complete-but-divergent cases, reviewed evidence for every remaining exclusion, and a mandatory revalidation flag for any downstream revision change.

## Measured corpus and configuration coverage

The differential corpus contains **12 source fixtures and 13 fixture/configuration cases**: **8 supported**, **4 explicitly non-complete**, and **1 intentionally incomplete under open-world defaults**. There are no accepted complete-but-semantically-divergent cases.

Supported coverage includes:

- nested conditional selection;
- source-order `#define` / `#undef` / redefinition behavior;
- multiline and nested macro expansion;
- function-like macros, variadics, stringification, token pasting, and rescanning;
- bounded numeric preprocessing conditions;
- both configured conditional outcomes;
- the C-GULL-oriented `offsetof` / container-recovery expression shape.

For every supported differential case, the harness checks preprocessing-token equivalence, original token line equivalence, `pycparser` parseability, and deterministic repeated output. The `offsetof` fixture additionally verifies the parsed recovery AST shape required by downstream container/layout analysis.

The explicitly non-complete cases are not treated as successful equivalence:

- raw reachable include directives: `unsupported_preprocessing_directive`;
- active `__VA_OPT__`: `unsupported_macro_expansion`;
- unmodeled predefined macro value uses such as `__LINE__`: `unsupported_macro_expansion`;
- reachable unsupported nonconditional directives such as `#pragma`: `unsupported_preprocessing_directive`.

These cases expose no partial `source`, `source_map`, or macro snapshot.

## Reviewed C-GULL migration boundary

The readiness verdict applies to **prepared translation-unit text**, not arbitrary compiler input.

1. C-GULL expands resolvable project headers before cpre and preserves header provenance.
2. Remaining unresolved `#include`, `#include_next`, or `#import` directives are masked with physical line endings preserved before calling cpre. This matches the information boundary of the current pcpp+pycparser path.
3. C-GULL translates build/profile values into `MacroConfiguration` and uses the closed unknown-name policy where pcpp-compatible concrete behavior is required. Source definitions still override external configuration in source order.
4. C-GULL consumes `PreprocessResult.source` only when `complete` is true; an incomplete result is coverage degradation, not clean output.
5. The pycparser typedef prelude remains a parser-integration step after preprocessing.

The pinned downstream corpus contains no `__VA_OPT__` use. A future occurrence is outside this readiness profile until cpre supports it or new reviewed non-impact evidence is recorded.

## What “ready” does and does not mean

The gate establishes that a downstream C-GULL migration issue may cite #28 as evidence that replacing pcpp is safe **within this pinned input profile and adapter contract**.

It does not mean:

- cpre is a general-purpose compiler preprocessor;
- cpre resolves arbitrary raw headers;
- every compiler predefined macro is implemented;
- a future C-GULL revision automatically inherits this verdict;
- a migration implementation may ignore cpre incomplete diagnostics.

If the downstream revision changes, the migration candidate must re-run the input inventory and confirm that the boundary still holds. Any new unsupported built-in, directive, include expectation, or `__VA_OPT__` occurrence reopens the relevant capability question before pcpp is removed.

## Revalidation procedure for the C-GULL migration PR

At the actual downstream migration commit:

1. confirm the C-GULL revision/profile being migrated;
2. re-run the source inventory for `__VA_OPT__` and preprocessing directives;
3. confirm project-header expansion still precedes cpre and preserves source provenance;
4. confirm unresolved includes are masked without changing physical line count;
5. translate concrete macro configuration through `MacroConfiguration` with the reviewed unknown-name policy;
6. inject the typedef prelude after cpre;
7. treat every non-complete cpre result as coverage degradation;
8. run downstream security, diagnostic, source-location, and configuration-profile regression tests before deleting pcpp.

A changed downstream profile does not invalidate cpre generally; it invalidates the **specific #28 readiness evidence** until the new profile is reviewed.

## Reproduce the cpre-side evidence

```bash
python -m pip install -e ".[dev]"
python -m pytest -q \
  tests/test_downstream_compatibility.py \
  tests/test_pcpp_differential.py \
  tests/test_cgull_migration_profile.py
python -m pytest -q
```

Repository CI runs the full supported Python/platform matrix plus installed-wheel/build validation. The compatibility manifests are cross-checked so supported configurations cannot silently disappear and explicit non-complete cases remain structured and reviewable.
