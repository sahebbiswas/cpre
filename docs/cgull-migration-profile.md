# C-GULL preprocessing migration input profile

This document defines the reviewed input boundary for replacing C-GULL's `pcpp`
preprocessing tier with `cpre.preprocess_source`. It resolves the scope question in
issue #39; it does **not** claim general compiler-preprocessor compatibility.

The downstream evidence is pinned to
[C-GULL `c61b275`](https://github.com/sahebbiswas/cgull/tree/c61b27520624c074661afa0a615160944b31ab3d).
A migration against a newer C-GULL revision must re-run the inventory below and
review any new preprocessing constructs before relying on this profile.

## Boundary and ownership

The supported migration boundary is **prepared translation-unit text**, not an
arbitrary raw compiler input. C-GULL remains responsible for file-system and build
policy around that boundary:

1. **Resolve project headers first.** C-GULL's `TUIncludeExpander` runs before
   `CASTParser` in the project-analysis path. Quote includes search the including
   source directory first and then configured include roots; angle includes search
   configured include roots. Resolved headers are expanded depth-first and carry a
   line-provenance map. C-GULL's scanner integration test also verifies that a
   vulnerability originating in an expanded header is still found and reported at
   the original header location.
2. **Mask unresolved includes before cpre.** System or otherwise unresolved include
   directives may remain after TU expansion. The cpre migration adapter must replace
   every remaining *active* `#include`, `#include_next`, or `#import` logical
   directive with whitespace while preserving all physical line endings before
   calling `preprocess_source`. This matches C-GULL's current pcpp tier, which lets
   unresolvable includes pass through and then strips remaining preprocessor
   directives before pycparser. cpre itself does not become an include resolver.
3. **Pass concrete macro configuration explicitly.** Build/profile values belong to
   C-GULL and are translated into `MacroConfiguration`, using the closed unknown-name
   policy defined by issue #37 when reproducing the current pcpp configuration
   semantics. Source `#define`/`#undef` directives continue to override external
   state in source order. C-GULL's `offsetof` replacement remains an injected
   replacement-text macro rather than an implicit cpre built-in.
4. **Run cpre atomically.** C-GULL may consume `result.source` only when
   `result.complete` is true. An incomplete result is coverage degradation and must
   not be interpreted as an empty/clean preprocessing result.
5. **Inject the pycparser typedef prelude afterwards.** C-GULL's
   `_PYCPARSER_PRELUDE` remains owned by the parser integration. The current parser
   filters and prepends that typedef prelude after preprocessing, so it is not part
   of the cpre input contract and must not be used to hide an unsupported
   preprocessing construct.

The versioned machine-readable summary is
[`tests/compatibility/cgull-profile.json`](../tests/compatibility/cgull-profile.json).

## Include exclusion: rationale and downstream impact evidence

Raw reachable includes remain outside cpre's supported preprocessing surface. This
is intentional for the C-GULL migration because C-GULL already owns translation-unit
include expansion:

- [`cgull/includes.py`](https://github.com/sahebbiswas/cgull/blob/c61b27520624c074661afa0a615160944b31ab3d/cgull/includes.py)
  implements bounded, provenance-preserving project-header expansion and leaves
  unresolved external/system headers unresolved.
- [`cgull/project_analysis.py`](https://github.com/sahebbiswas/cgull/blob/c61b27520624c074661afa0a615160944b31ab3d/cgull/project_analysis.py)
  expands a source with `TUIncludeExpander` before passing the resulting text to
  `CASTParser`.
- [`tests/test_includes.py`](https://github.com/sahebbiswas/cgull/blob/c61b27520624c074661afa0a615160944b31ab3d/tests/test_includes.py)
  contains scanner-level coverage proving a finding from an expanded header is
  retained with the header's original file and line provenance.
- The current `_SilentPreprocessor` path in
  [`cgull/ast_analyzer/visitor.py`](https://github.com/sahebbiswas/cgull/blob/c61b27520624c074661afa0a615160944b31ab3d/cgull/ast_analyzer/visitor.py)
  passes through unresolvable includes and then blanks remaining preprocessor
  directive lines before pycparser. Preserving that masking step at the cpre adapter
  boundary therefore does not remove information that the current AST tier consumes.

This is a caller policy, not evidence that arbitrary headers are irrelevant. A
resolved project header must still be expanded before cpre so its declarations,
macros, and security-relevant code participate in analysis. If C-GULL later changes
the boundary so raw reachable includes are expected to reach cpre, include
resolution becomes a new required cpre capability and this exclusion must be
re-reviewed.

The cpre regression suite keeps both sides of this boundary explicit: a prepared TU
with resolved header content and a masked unresolved include line must preprocess
and parse successfully, while a raw reachable include must still fail atomically
with `unsupported_preprocessing_directive`.

## `__VA_OPT__` exclusion: rationale and downstream impact evidence

At pinned C-GULL commit `c61b275`, a repository-wide search for the literal
`__VA_OPT__` returns **zero occurrences**. The agreed migration corpus therefore does
not require C++20/C23 `__VA_OPT__` semantics. Standard variadic macros using
`__VA_ARGS__` remain inside the supported cpre compatibility surface.

The exclusion is deliberately narrow:

- cpre continues to return atomic `unsupported_macro_expansion` when an active
  `__VA_OPT__` expansion is encountered;
- the compatibility fixture `unsupported_va_opt.c` remains in the known-difference
  set, so the gap cannot silently disappear from the readiness report;
- any future C-GULL migration candidate containing `__VA_OPT__` is outside this
  profile and requires a separately reviewed cpre feature issue before the
  replacement gate can remain satisfied.

`pcpp` is not used as a GCC/Clang compatibility oracle for this decision. The
non-impact evidence is the absence of the construct from the agreed downstream
corpus plus the explicit failure contract when it is encountered.

## Review inventory for a newer downstream revision

Before citing this profile for a different C-GULL commit, reviewers should at least:

```bash
git grep -n '__VA_OPT__' -- '*.c' '*.h' '*.cc' '*.cpp' '*.cxx' '*.hpp'
git grep -nE '^[[:space:]]*#[[:space:]]*(include|include_next|import)\b' -- \
  '*.c' '*.h' '*.cc' '*.cpp' '*.cxx' '*.hpp'
```

Then confirm that project headers still pass through C-GULL's include expander before
cpre, remaining unresolved include directives are masked without changing physical
line count, the typedef prelude is still injected after cpre, and the concrete macro
configuration adapter matches the reviewed build/profile inputs.

## Out-of-profile behavior

Inputs outside this profile remain explicit rather than best-effort:

- reachable raw includes: `unsupported_preprocessing_directive`;
- active `__VA_OPT__`: `unsupported_macro_expansion`;
- any other unsupported or unresolved construct: the corresponding structured
  incomplete diagnostic, with no partial `source`, `source_map`, or macro snapshot.

That atomic failure behavior is part of the migration safety case. The profile is a
bounded downstream contract, not permission to silently strip or guess at new
language features.