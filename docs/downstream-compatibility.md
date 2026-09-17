# Downstream compatibility contract

This document defines the supported `cpre` surface for library consumers such as static analyzers. Consumers should import from the top-level `cpre` package only and should not depend on `cpre.cpre`, `cpre.model`, `cpre.expressions`, ROBDD internals, private helpers, CLI rendering, or incidental expression formatting beyond the documented public helpers and replacement strings.

## Supported contract

The compatibility suite in `tests/test_downstream_contract.py` is the executable contract for downstream integrations. It covers:

- documented top-level imports
- public symbolic expression categories and deterministic local Boolean algebra
- stable tagged symbolic expression interchange
- `AnalysisResult` and ordered `Finding` results
- stable `FindingKind` categories
- one-based source locations and end-exclusive physical edit ranges
- globally equivalent `ExactSimplification` results
- context-dependent `ContextualSimplification` results
- dead and redundant branch classification
- opaque predicate reporting
- deterministic result ordering
- optional `SuggestedEdit` metadata and `FixConfidence`

`SuggestedEdit` is intentionally optional. Exact condition rewrites may be used as context-independent mechanical fixes. Contextual rewrites are valid only under the branch context and should be treated as lower-confidence suggestions. Dead/redundant classification and macro-form directives do not imply a mechanical edit unless `cpre` explicitly returns one.

## Symbolic expression compatibility

The top-level symbolic expression surface is compatibility-sensitive for downstream analyzers that need to share preprocessor conditions without importing implementation modules.

Supported semantic categories are:

- `Constant` (`TRUE` / `FALSE`)
- `Variable` for macro truth/value
- `DefinedVariable` for macro definedness
- `Predicate` for opaque predicates
- `Negation`, `Conjunction`, and `Disjunction`
- the `Expression` and `BooleanAtom` typing aliases

Macro truth and macro definedness are separate facts. `Variable("A")` and `DefinedVariable("A")` must never be collapsed merely because they share a macro name, and `Predicate("A")` remains an opaque predicate rather than becoming a macro-value atom.

The supported algebra helpers are `negate`, `conjunction`, `disjunction`, `simplify`, and `normalize`. They apply deterministic local Boolean identities only; exact ROBDD proof operations are a separate API concern. `format_expression` normalizes before rendering, and `ordered_atoms` gives deterministic semantic atom enumeration independent of set/hash iteration.

`expression_to_dict` / `expression_from_dict` are the supported structured interchange boundary. The tagged kinds `constant`, `variable`, `defined`, `predicate`, `not`, `and`, and `or` preserve semantic categories without depending on dataclass module paths or `repr()` output. Encoded expressions are canonicalized deterministically. Unknown kinds, missing/extra fields, and wrong JSON value shapes are rejected instead of coerced.

Patch releases must not deliberately reshape these categories, their semantic distinction, deterministic formatting/normalization behavior, or the tagged interchange meaning. Intentional incompatibilities follow the versioning rules below.

## Host-owned pragma compatibility

cpre supports standard `#pragma` syntax and `_Pragma` destringization/dispatch, but pragma **meaning is not part of the core compatibility contract**. A downstream analyzer that needs reachable pragmas must provide `pragma_handler` and explicitly return `PragmaDisposition.CONSUME` only for payloads its analysis profile can safely account for. Unknown or rejected pragmas remain atomic `unsupported_preprocessing_directive` results; discarded-branch pragmas are not dispatched.

Both source `#pragma` and macro-generated `_Pragma` use the same `Pragma` callback surface with physical source provenance. cpre does not emulate GCC, Clang, MSVC, OpenMP/OpenACC, packing, diagnostic, optimization, or other implementation-defined pragma semantics. See [Standard pragma syntax and host-owned semantics](pragma-handling.md).

## pcpp replacement compatibility gate

Before a downstream consumer removes an existing `pcpp` preprocessing path in favor of `cpre.preprocess_source`, the corpus in `tests/compatibility/fixtures` must pass `tests/test_pcpp_differential.py` for every supported fixture/configuration relevant to that consumer.

**Current C-GULL verdict: ready for the bounded profile pinned by issue #28.** See the [versioned readiness report](pcpp-readiness.md) and [C-GULL migration profile](cgull-migration-profile.md). This readiness result is profile-specific: a newer downstream revision must revalidate the inventory and adapter boundary before relying on it.

The gate requires:

- equivalent macro-expanded preprocessing token streams after normalizing only comments, whitespace, and line-marker formatting
- equivalent original token line positions after resolving pcpp's `#line` directives, so source-coordinate-sensitive analysis does not silently drift
- parse-ready output from both preprocessors through `pycparser`
- deterministic repeated output
- explicit `EXPLICIT_NONCOMPLETE_CASES` entries, with reasons, for every corpus fixture not covered by the equivalence set
- explicit coverage of the C-GULL-oriented `offsetof`/container recovery shape
- reviewed downstream non-impact evidence for every explicit non-complete case that remains outside the migration profile

A new mismatch must either be fixed or deliberately added to `EXPLICIT_NONCOMPLETE_CASES` with a linked issue and reviewable rationale; the harness must not be weakened merely to make a migration pass. Every exclusion blocks a new or changed migration profile until fixed or accompanied by reviewed evidence of non-impact. Constructs detected as unsupported fail atomically: no source, source map, or macro snapshot is exposed. The gate does not permit known complete-but-semantically-divergent output to be treated as an accepted difference.

`pcpp` is a development/test dependency used only as the differential oracle. It is not part of cpre's runtime dependency surface.

## Compatibility and versioning

During the current `0.x` phase, `cpre` treats the documented public API as compatibility-sensitive even though semantic versioning traditionally permits breaking changes before `1.0`.

- Patch releases must preserve documented imports and established result semantics. They may fix incorrect behavior and add compatible surface area without deliberately reshaping existing contracts.
- Minor releases may add broader backward-compatible public fields, types, finding categories, or capabilities. Downstream consumers should still review new finding kinds if they use exhaustive matching.
- Any deliberate incompatible change to the documented downstream contract requires an explicitly announced compatibility break and a minor-version boundary while `cpre` remains `0.x`.
- After `1.0`, intentionally breaking public API changes require a major version bump.

Downstream projects should pin a compatible release range and rely only on behavior covered by the public documentation and contract suite.

## Installed-package validation

CI builds the wheel, installs that wheel, copies the downstream contract test outside the repository checkout, and runs it against the installed package. CI also verifies `python -m cpre --help`. This catches missing package files, incorrect exports, symbolic API packaging drift, and other packaging-only failures that editable-source tests can miss.
