# C-GULL symbolic preprocessor migration readiness

This document is the versioned readiness record for replacing C-GULL's local symbolic-preprocessor implementation with cpre's supported public symbolic APIs. It is deliberately separate from [`cgull-migration-profile.md`](cgull-migration-profile.md), which covers the concrete `pcpp` replacement boundary around `cpre.preprocess_source()`.

The executable gate is `tests/test_cgull_symbolic_migration_profile.py`; its machine-readable pin and inventory are in `tests/compatibility/cgull-symbolic-profile.json`.

## Validation pins

- **C-GULL:** `sahebbiswas/cgull@265f7bfdcb806a775a88510483ba28552f50f006` (`main` on 2026-09-17).
- **cpre implementation baseline:** `sahebbiswas/cpre@88e14949e594c1739467b40ea53210e56bb56771`.
- **cpre version under test:** `0.11.0`.
- **Latest published cpre release at validation:** `0.10.16`.

The pin matters: C-GULL owns policy layered on these primitives, and a newer downstream revision can add new assumptions even when cpre itself has not changed.

## C-GULL dependency inventory

The pinned C-GULL revision carries the symbolic implementation in:

- `cgull/preprocessor/expressions.py`
- `cgull/preprocessor/directives.py`
- `cgull/preprocessor/robdd.py`
- `cgull/preprocessor/branch_analysis.py`
- `cgull/preprocessor/configuration_space.py`
- `cgull/preprocessor/profile_reduction.py`

Direct consumers outside those implementation modules include:

- `cgull/__init__.py`
- `cgull/ast_analyzer/preprocessor.py`
- `cgull/compile_database.py`
- `cgull/preprocessor_cli.py`
- `cgull/rules/preprocessor_reachability.py`
- `cgull/rules/preprocessor_simplification.py`

Representative downstream tests and documentation reviewed for the pin include `tests/test_conditional_directives.py`, `tests/test_issue_423_preprocessor_reachability.py`, `tests/test_issue_424_preprocessor_simplification.py`, `tests/test_preprocessor_configuration_space.py`, `tests/test_config_profile_reduction.py`, `tests/test_preprocessor_concrete_ir.py`, and `docs/analysis/symbolic-preprocessor.md`.

## Supported public API boundary

C-GULL may migrate through top-level `cpre` imports only. The readiness gate exercises the following supported families:

### Symbolic expressions

`Constant`, `Variable`, `DefinedVariable`, `Predicate`, `Negation`, `Conjunction`, `Disjunction`, `TRUE`, `FALSE`, `conjunction`, `disjunction`, `negate`, `normalize`, `format_expression`, `ordered_atoms`, `expression_to_dict`, and `expression_from_dict`.

This preserves the important distinction between bare macro truth, macro definedness, and opaque predicates, while retaining deterministic normalization, formatting, atom ordering, and structured interchange.

### Lossless conditional structure

`parse_conditionals` plus the public structural tree/block/branch/directive/token/range/diagnostic types. The gate covers nested blocks, `#if/#ifdef/#ifndef/#elif/#elifdef/#elifndef/#else/#endif`, physical offsets and half-open ranges, continuation lines, comment/literal masking, source-order diagnostics, and recoverable malformed structure.

### Exact Boolean queries and witnesses

`satisfiable`, `implies`, `equivalent`, `exact_simplify`, and `witness_assignment`, with `AnalysisOptions` and structured result types. The gate verifies exact proof results, deterministic witnesses, distinct atom categories, and explicit incomplete results for deterministic resource-limit exhaustion.

The gate does not import `cpre.model`, `cpre.expressions`, `cpre.structure`, `cpre.proofs`, `cpre.robdd`, or any ROBDD manager/node API directly.

## End-to-end semantic compatibility

The compatibility test reconstructs representative C-GULL branch-analysis behavior using public cpre primitives and checks semantic outputs rather than implementation identity. It covers:

- source/preorder branch ordering;
- reachable versus dead branches;
- redundancy under enclosing/sibling context;
- contextual simplification (`ROOT && CHILD` under `ROOT` simplifies to `CHILD`);
- effective branch conditions across `#if/#elif/#else` chains;
- deterministic configuration witnesses derived from enclosing context;
- macro-value versus definedness versus opaque-predicate witness assignments;
- `UNSAT` versus resource-limit `INCOMPLETE` handling;
- malformed structural input remaining structured and source-located.

There are **zero accepted complete-but-semantically-divergent cases** in this gate.

## Accepted downstream-owned differences

Two naming differences are explicit and tested rather than normalized away silently:

1. C-GULL's witness adapter currently names the definedness assignment category `defined`; cpre's public enum names the same semantic category `macro_defined`. The mapping is one-to-one and remains distinct from `macro_value`.
2. Four structural diagnostic code names differ while preserving the same stable category and source range: C-GULL `invalid_macro`, `unexpected_tokens`, `misplaced_directive`, and `unterminated_block` map respectively to cpre `malformed_macro_directive`, `trailing_directive_text`, `unmatched_directive`, and `unterminated_conditional`.

These mappings belong in the C-GULL migration adapter. They are not permission to collapse atom categories, source ranges, proof completion state, or malformed-input semantics.

## Ownership after migration

**cpre owns:** symbolic expression representation/algebra; lossless conditional structure and source ranges; exact Boolean proof/witness primitives; compatibility/versioning of those top-level APIs.

**C-GULL owns:** rule IDs/messages/severity; scan-profile orchestration; build and translation-unit policy; mapping cpre semantic facts into findings; C-GULL-specific configuration-profile selection/reduction policy.

In particular, `profile_reduction.py` contains C-GULL policy over symbolic branch facts. Migration should replace its duplicated low-level expression/BDD machinery with public cpre facts where practical, not move C-GULL profile policy into cpre.

## Installed-wheel gate

Repository CI builds a wheel, force-installs it, copies the symbolic migration test and pinned metadata outside the repository checkout, and runs the gate there. This proves the migration surface is actually packaged and available through top-level imports rather than succeeding because source-tree private modules are importable.

## Final readiness statement

**READY for cpre 0.11.0 artifacts built from the validated public API baseline, with the two explicit adapter-name mappings above.** Version `0.11.0` is the first cpre release suitable for the symbolic portion of `sahebbiswas/cgull#436`, provided the release is built from a revision where this gate remains green.

The latest published release at validation, **v0.10.16, is NOT READY** for the symbolic migration because it predates the public expression, lossless structural, and exact proof/witness surfaces completed by cpre issues #60, #61, and #62. C-GULL should pin `0.11.0` only after that release is actually published; until then, this is release-candidate readiness, not permission to depend on unreleased `main`.

Concrete `pcpp` replacement readiness is a separate result and remains governed by [`cgull-migration-profile.md`](cgull-migration-profile.md) and [`pcpp-readiness.md`](pcpp-readiness.md).

## Revalidating a newer C-GULL revision

For a later C-GULL commit:

1. update the downstream commit pin in `tests/compatibility/cgull-symbolic-profile.json`;
2. re-inventory the six symbolic modules plus direct consumers/tests/docs, including new imports of `cgull.preprocessor` symbols;
3. port any new representative branch/configuration semantics into `tests/test_cgull_symbolic_migration_profile.py` using top-level `cpre` imports only;
4. review every mismatch as either a cpre defect, a C-GULL defect, or an explicitly documented downstream-owned difference with non-impact evidence;
5. run the full test matrix and the installed-wheel contract job;
6. update this readiness statement only after the revised pin is green.

Do not revalidate by comparing CLI text alone, by importing cpre private modules, or by weakening source-range/proof/category/completion assertions.
