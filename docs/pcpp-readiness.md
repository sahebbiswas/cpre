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
| [#38: built-ins/directives](https://github.com/sahebbiswas/cpre/issues/38) | Resolved | Unsupported predefined macro value uses and unsupported reachable directives return structured atomic non-complete results rather than silently divergent output. |
| [#51: standard predefined context](https://github.com/sahebbiswas/cpre/issues/51) | Resolved | `__LINE__`, `__FILE__`, standard `#line`, and explicitly configured standard environment values have deterministic analyzer-oriented semantics with physical provenance preserved. |
| [#39: unsupported-input scope](https://github.com/sahebbiswas/cpre/issues/39) | Resolved by reviewed profile | Project headers are expanded before cpre; unresolved include lines are masked at the adapter boundary; the pinned C-GULL corpus has zero unsupported preprocessing constructs required by the profile. |
| Final downstream validation | Satisfied for pinned profile | The pinned revision was still current C-GULL `main`; the include-expansion/source-provenance boundary and current pcpp masking behavior remain the reviewed migration boundary. |

The machine-readable profile records `status: ready`, zero supported-case divergences, zero accepted complete-but-divergent cases, reviewed evidence for every remaining exclusion, and a mandatory revalidation flag for any downstream revision change.

## Measured corpus and configuration coverage

The differential corpus contains **11 source fixtures and 12 fixture/configuration cases**: **9 supported**, **2 explicitly unsupported**, and **1 intentionally incomplete under open-world defaults**. There are no accepted complete-but-semantically-divergent cases.

Supported coverage includes:

- nested conditional selection;
- source-order `#define` / `#undef` / redefinition behavior;
- multiline and nested macro expansion;
- function-like macros, variadics, `__VA_OPT__`, stringification, token pasting, and rescanning;
- bounded numeric preprocessing conditions;
- deterministic standard `__LINE__` expansion with physical coordinate comparison against pcpp;
- both configured conditional outcomes;
- the C-GULL-oriented `offsetof` / container-recovery expression shape.

For every supported differential case, the harness checks preprocessing-token equivalence, original token line equivalence, `pycparser` parseability, and deterministic repeated output. The `offsetof` fixture additionally verifies the parsed recovery AST shape required by downstream container/layout analysis.

The remaining non-complete cases are not treated as successful equivalence:

- raw reachable include directives: `unsupported_preprocessing_directive`;
- reachable pragmas without an explicit host `pragma_handler`, and other unsupported nonconditional directives: `unsupported_preprocessing_directive`;
- open-world conditions without enough configuration: `unresolved_condition`.

Standard pragma syntax/dispatch is now available as a host extension point; pragma meaning remains outside cpre core. A downstream profile may consume only the pragma payloads it explicitly accounts for, while unknown pragmas stay atomic incomplete results. See [pragma handling](pragma-handling.md).

Environment-dependent standard predefined values still require deterministic caller context. For example, `__DATE__`, `__TIME__`, or a language-mode macro used without an explicit value remains atomic `unsupported_macro_expansion`. These cases expose no partial `source`, `source_map`, or macro snapshot.

## Reviewed C-GULL migration boundary

The readiness verdict applies to **prepared translation-unit text**, not arbitrary compiler input.

1. C-GULL expands resolvable project headers before cpre and preserves header provenance.
2. Remaining unresolved `#include`, `#include_next`, or `#import` directives are masked with physical line endings preserved before calling cpre. This matches the information boundary of the current pcpp+pycparser path.
3. C-GULL translates build/profile values into `MacroConfiguration` and deterministic standard-environment values into `PreprocessingContext`, using the closed unknown-name policy where pcpp-compatible concrete behavior is required. Source definitions still override ordinary external macro configuration in source order.
4. C-GULL consumes `PreprocessResult.source` only when `complete` is true; an incomplete result is coverage degradation, not clean output.
5. The pycparser typedef prelude remains a parser-integration step after preprocessing.

A downstream adapter does not need to synthesize physical locations for logical `#line` changes: cpre preserves physical `SourceLocation` and `source_map` provenance while applying logical line/file state only to standard macro expansion.

## What “ready” does and does not mean

The gate establishes that a downstream C-GULL migration issue may cite #28 as evidence that replacing pcpp is safe **within this pinned input profile and adapter contract**.

It does not mean:

- cpre is a general-purpose compiler preprocessor;
- cpre resolves arbitrary raw headers;
- every compiler predefined macro is implemented;
- cpre infers vendor, target, ABI, language-mode, optimization, date, or time state from the host environment;
- a future C-GULL revision automatically inherits this verdict;
- a migration implementation may ignore cpre incomplete diagnostics.

If the downstream revision changes, the migration candidate must re-run the input inventory and confirm that the boundary still holds. Any new unsupported built-in, directive, include expectation, or other preprocessing construct reopens the relevant capability question before pcpp is removed.

## Revalidation procedure for the C-GULL migration PR

At the actual downstream migration commit:

1. confirm the C-GULL revision/profile being migrated;
2. re-run the source inventory for preprocessing directives and predefined-macro requirements;
3. confirm project-header expansion still precedes cpre and preserves source provenance;
4. confirm unresolved includes are masked without changing physical line count;
5. translate concrete macro configuration through `MacroConfiguration` with the reviewed unknown-name policy;
6. supply deterministic standard environment values through `PreprocessingContext` when the source requires them;
7. inject the typedef prelude after cpre;
8. treat every non-complete cpre result as coverage degradation;
9. run downstream security, diagnostic, source-location, and configuration-profile regression tests before deleting pcpp.

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