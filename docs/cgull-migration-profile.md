# C-GULL preprocessing migration input profile

This document defines the reviewed input boundary for replacing C-GULL's `pcpp` preprocessing tier with `cpre.preprocess_source`. It resolves the scope question in issue #39 and is the bounded downstream profile used to satisfy readiness issue #28. It does **not** claim general compiler-preprocessor compatibility.

The downstream evidence is pinned to [C-GULL `c61b275`](https://github.com/sahebbiswas/cgull/tree/c61b27520624c074661afa0a615160944b31ab3d). During final #28 validation on 2026-09-12, that commit remained C-GULL `main`, and a fresh repository search still found zero `__VA_OPT__` occurrences. The profile is therefore **ready for migration at this pinned revision**. A migration against any newer C-GULL revision must re-run the inventory below and review any new preprocessing constructs before relying on this verdict.

The machine-readable profile is [`tests/compatibility/cgull-profile.json`](../tests/compatibility/cgull-profile.json); its readiness section is part of the executable gate.

## Boundary and ownership

The supported migration boundary is **prepared translation-unit text**, not arbitrary raw compiler input. C-GULL remains responsible for file-system and build policy around that boundary:

1. **Resolve project headers first.** `TUIncludeExpander` runs before `CASTParser` in the project-analysis path. Resolved headers are expanded depth-first and carry line provenance. Scanner regression coverage verifies that a finding originating in an expanded header is retained at the original header file/line.
2. **Mask unresolved includes before cpre.** Any remaining active `#include`, `#include_next`, or `#import` logical directive must be replaced with whitespace while preserving physical line endings before `preprocess_source`. This matches the current pcpp+pycparser information boundary, where unresolved directives are not consumed by the AST tier.
3. **Pass concrete macro configuration explicitly.** Build/profile values belong to C-GULL and are translated into `MacroConfiguration`, using the closed unknown-name policy where pcpp-compatible concrete behavior is required. Source `#define`/`#undef` still override external state in source order. C-GULL's `offsetof` replacement remains an injected replacement-text macro rather than an implicit cpre built-in.
4. **Run cpre atomically.** C-GULL may consume `result.source` only when `result.complete` is true. An incomplete result is coverage degradation and must not be interpreted as empty or clean preprocessing output.
5. **Inject the pycparser typedef prelude afterwards.** `_PYCPARSER_PRELUDE` remains parser-integration state after preprocessing and must not be used to hide unsupported preprocessing.

## Include exclusion: reviewed non-impact evidence

Raw reachable includes remain outside cpre's supported preprocessing surface. This is intentional because C-GULL already owns translation-unit include expansion:

- `cgull/includes.py` implements bounded, provenance-preserving project-header expansion;
- `cgull/project_analysis.py` expands source through `TUIncludeExpander` before `CASTParser`;
- `tests/test_includes.py::test_tu_include_expansion_integration_with_scanner` verifies a security finding from an expanded header is preserved with original provenance;
- `cgull/ast_analyzer/visitor.py::_try_pcpp_preprocess` establishes the existing boundary where unresolved preprocessor directives do not become AST input.

The cpre regression suite models both sides explicitly: a prepared TU with resolved header content and a masked unresolved include line preprocesses and parses successfully, while a raw reachable include still fails atomically with `unsupported_preprocessing_directive`.

This is a caller policy, not evidence that headers are irrelevant. If C-GULL later expects raw reachable includes to reach cpre, include resolution becomes a new required capability and this readiness profile must be re-reviewed.

## `__VA_OPT__` exclusion: reviewed non-impact evidence

At pinned C-GULL commit `c61b275`, including the final #28 revalidation, repository-wide search returns **zero `__VA_OPT__` occurrences**. The agreed migration corpus therefore does not require C++20/C23 `__VA_OPT__` semantics. Standard `__VA_ARGS__` behavior remains inside the supported cpre surface.

The exclusion stays narrow:

- active `__VA_OPT__` returns atomic `unsupported_macro_expansion`;
- the compatibility fixture remains explicitly non-complete so the gap cannot silently disappear;
- any future C-GULL occurrence is outside this profile until separately supported or reviewed.

The non-impact evidence is the downstream inventory plus explicit atomic failure, not pcpp's feature set.

## Final readiness statement

For the pinned revision, every known differential mismatch is either supported or represented as a structured non-complete case with reviewed downstream non-impact evidence. There are zero accepted complete-but-semantically-divergent cases. A downstream migration issue may cite cpre #28 as evidence that pcpp replacement is safe **provided this boundary is implemented unchanged and downstream regression validation remains green**.

This does not remove the migration PR's responsibility to validate its own adapter implementation. Before deleting pcpp, that PR must verify source-location/security/configuration behavior end-to-end and re-run the inventory if the C-GULL revision differs from the pin.

## Review inventory for a newer downstream revision

Before citing this profile for a different C-GULL commit, reviewers should at least run:

```bash
git grep -n '__VA_OPT__' -- '*.c' '*.h' '*.cc' '*.cpp' '*.cxx' '*.hpp'
git grep -nE '^[[:space:]]*#[[:space:]]*(include|include_next|import)\b' -- \
  '*.c' '*.h' '*.cc' '*.cpp' '*.cxx' '*.hpp'
```

Then confirm that project headers still pass through the include expander before cpre, unresolved include directives are masked without changing physical line count, the typedef prelude is still injected after cpre, the configuration adapter matches the reviewed inputs, and no newly unsupported predefined macro/directive is being silently relied on.

## Out-of-profile behavior

Inputs outside this profile remain explicit rather than best-effort:

- reachable raw includes: `unsupported_preprocessing_directive`;
- active `__VA_OPT__`: `unsupported_macro_expansion`;
- unmodeled predefined macro value uses: `unsupported_macro_expansion`;
- unsupported reachable nonconditional directives: `unsupported_preprocessing_directive`;
- any other unsupported or unresolved construct: the corresponding structured incomplete diagnostic, with no partial `source`, `source_map`, or macro snapshot.

That atomic failure behavior is part of the migration safety case. The profile is a bounded downstream contract, not permission to silently strip or guess at new language features.
