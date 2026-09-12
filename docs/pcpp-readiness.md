# C-GULL pcpp replacement readiness report

**Verdict: BLOCKED — do not remove C-GULL's runtime pcpp dependency yet.**

This report tracks [readiness issue #28](https://github.com/sahebbiswas/cpre/issues/28).
The downstream reference used for the compatibility evidence is
[C-GULL `c61b275`](https://github.com/sahebbiswas/cgull/tree/c61b27520624c074661afa0a615160944b31ab3d).
No C-GULL implementation changes are part of this gate.

Issues #36 and #37 are resolved on main: bounded numeric preprocessing conditions
are supported, and concrete migration configuration is represented by
`MacroConfiguration`, including a closed unknown-name policy. Issue #39 is resolved
by the reviewed [C-GULL migration input profile](cgull-migration-profile.md), which
records why raw includes and `__VA_OPT__` remain outside the bounded migration
surface. Issue #38 removes the remaining silent-completion gap: reachable predefined
macros that cpre does not model and reachable nonconditional directives now return
structured atomic non-complete results instead of `complete=True` output with
semantics that differ from pcpp. The overall replacement gate remains blocked on
final downstream migration validation and reviewed non-impact for every unsupported
construct encountered by the migration candidate.

## Measured corpus and configuration coverage

The versioned differential corpus contains **12 source fixtures and 13
fixture/configuration cases**: **8 supported**, **4 unsupported**, and **1 incomplete**.
There are no accepted complete-but-semantically-divergent cases. Seven distinct
source fixtures are supported. `FEATURE=True` and `FEATURE=False` are both exercised
for `configured_conditional.c`; source-local definitions are part of the fixtures.

| Fixture | Configuration | Classification / result |
| --- | --- | --- |
| `offsetof_container.c` | Source-local definitions | Supported; member-pointer subtraction and null-base member offset AST verified |
| `nested_conditionals.c` | Source-local flags | Supported; nested branch selection |
| `source_order.c` | Define, undefine, redefine | Supported; expansion follows source order |
| `multiline_nested_macros.c` | Source-local macros | Supported; nested expansion and continued definition |
| `general_macro_operators.c` | Source-local macros | Supported; `#`, `##`, rescanning |
| `incomplete_numeric_condition.c` | `FOO=3` defined in source | Supported after #36; concrete numeric condition selects the expected branch |
| `configured_conditional.c` | `FEATURE=True`, `FEATURE=False` | Supported in both open-world Boolean fixture configurations |
| `unsupported_include.c` | None | Atomic unsupported directive; accepted only outside the prepared C-GULL input boundary defined by #39 |
| `unsupported_va_opt.c` | None | Atomic unsupported expansion; pinned C-GULL corpus has zero `__VA_OPT__` occurrences per #39 |
| `incomplete_unknown_condition.c` | None | Intentionally incomplete under open-world defaults; C-GULL migration uses the closed concrete configuration policy from #37 |
| `divergent_builtin_line.c` | None | Atomic `unsupported_macro_expansion` for unmodeled `__LINE__`; no partial source/map/macro snapshot is exposed |
| `divergent_pragma.c` | None | Atomic `unsupported_preprocessing_directive` for reachable `#pragma once`; no retained directive is certified as complete |

For each supported differential case, the harness checks exact preprocessing-token
equality, original token line equality, parseability through `pycparser`, and
deterministic repeated output. Only whitespace, comments, and line-marker formatting
are normalized. cpre preserves physical lines; the oracle's compact output is mapped
through its `#line` directives. Expanded columns must use cpre's `SourceMapping`
invocation ranges rather than assuming output columns equal source columns.

The `offsetof` test verifies the parsed recovery expression, preserving the AST shape
needed by downstream container/layout analysis. Configuration-specific replacement
text, including the C-GULL `offsetof` definition, is supplied through the concrete
configuration policy introduced by #37 rather than being treated as an implicit
cpre built-in.

## Resolved and remaining readiness items

| Follow-up | Status | Evidence / remaining work |
| --- | --- | --- |
| [#36: numeric conditions](https://github.com/sahebbiswas/cpre/issues/36) | Resolved | Bounded numeric evaluation is in the supported differential set. |
| [#37: configuration policy](https://github.com/sahebbiswas/cpre/issues/37) | Resolved | `MacroConfiguration` supports concrete definitions and an opt-in closed unknown-name policy while preserving the symbolic/open-world API. |
| [#39: unsupported-input scope](https://github.com/sahebbiswas/cpre/issues/39) | Resolved by reviewed profile | C-GULL owns project-header expansion; unresolved include directives are masked at the adapter boundary exactly where the current AST path already discards them. The pinned downstream corpus has zero `__VA_OPT__` occurrences. Raw includes and `__VA_OPT__` remain atomic unsupported inputs. |
| [#38: built-ins and directives](https://github.com/sahebbiswas/cpre/issues/38) | Resolved by explicit non-complete contract | Known predefined macros without concrete replacement semantics return `unsupported_macro_expansion`; reachable unsupported nonconditional directives return `unsupported_preprocessing_directive`. Diagnostics are structured and atomic, with no stderr side channel. |
| Final downstream migration validation | **Blocking** | Re-run the pinned/current C-GULL input inventory and security/source-location validation before removing pcpp. Any occurrence of an explicitly unsupported construct needs support or reviewed adapter/non-impact evidence. |

The #39 decision is deliberately a caller-policy boundary, not a claim that headers
are irrelevant. C-GULL must expand resolvable project headers before cpre so their
macros, declarations, code, and provenance remain visible to security analysis.
C-GULL already has scanner-level regression coverage demonstrating that a finding
originating in an expanded header is retained and mapped back to the original header
file and line. Remaining unresolved/system includes are not consumed by the current
pcpp+pycparser tier either; that tier passes them through and then blanks remaining
preprocessor directive lines before parsing.

Likewise, the `__VA_OPT__` exclusion is tied to the pinned downstream revision. A
future migration candidate containing `__VA_OPT__` is outside the reviewed profile
and requires a separately reviewed capability before the gate can remain satisfied.
The decision is based on downstream corpus inventory, not on treating pcpp as a
GCC/Clang compatibility oracle.

## Built-in and retained-directive policy

`preprocess_source` does not synthesize environment-dependent values for predefined
macros it cannot model deterministically. Reachable uses of known predefined names
such as `__LINE__`, `__FILE__`, `__DATE__`, `__TIME__`, `__COUNTER__`, and the standard
`__STDC*` family return `unsupported_macro_expansion` unless the macro has explicit
concrete replacement text in the active macro environment. This rule applies to
ordinary retained source, reachable conditional expressions, and predefined names
produced by another macro expansion. Ordinary unknown C identifiers are not rejected,
and matching spellings inside comments or literals remain ordinary text.

Reachable nonconditional preprocessing directives outside the supported `#define`,
`#undef`, and conditional set — including `#line`, `#pragma`, `#error`, `#warning`,
and implementation-specific directives — return
`unsupported_preprocessing_directive`. A null `#` directive is harmless and masked.
Unsupported directives inside discarded branches do not block preprocessing.
`#error` and `#warning` are diagnostics in the returned result only; cpre does not
write them directly to stderr.

Both policies are atomic: `source`, `source_map`, and `macros` are `None` on the
non-complete result. Because no output is certified, cpre does not invent a source
mapping for semantics it did not perform. Downstream tools may model environment
macros explicitly through `MacroConfiguration`; Boolean assumptions alone do not
supply replacement text.

## Public compatibility contract

Use the top-level `preprocess_source`, `PreprocessResult`, `MacroConfiguration`,
macro types, `SourceMapping`, and structured errors documented in the
[API guide](api.md#concrete-conditional-selection). Unknown reachable conditions and
detected unsupported preprocessing return incomplete results atomically; consumers
must not treat incomplete output as a clean preprocessing result.

For the C-GULL migration, the concrete boundary is defined in
[`cgull-migration-profile.md`](cgull-migration-profile.md) and enforced by
`tests/test_cgull_migration_profile.py` plus the machine-readable
`tests/compatibility/cgull-profile.json`. The profile pins the downstream revision,
include policy, configuration ownership, typedef-prelude ordering, and unsupported
construct inventory.

`complete=True` certifies cpre's documented supported selection and expansion
operations and, importantly, no longer silently permits the known #38 predefined
macro/directive cases. It still does not claim general GCC/Clang preprocessing
equivalence, header resolution, or arbitrary implementation-specific built-ins.

## Release decision procedure

1. Keep the public API contract and installed-wheel contract checks passing.
2. Run the focused compatibility/profile tests and the full suite on the candidate
   commit and CI platform matrix.
3. Keep every supported fixture/configuration in the equivalence set. Every explicit
   non-complete case must retain a reviewable rationale, downstream impact evidence,
   and regression coverage; an allowlist name alone is not sufficient evidence.
4. At the actual C-GULL migration commit, re-run the input-profile inventory. Confirm
   project headers still expand before cpre, unresolved include directives are masked
   with physical line preservation, concrete configuration is translated through the
   reviewed policy, the typedef prelude is injected after cpre, and every unsupported
   built-in/directive/`__VA_OPT__` case is absent or separately handled with reviewed
   evidence.
5. Validate downstream security findings, source locations, diagnostics, and
   configuration profiles before removing pcpp from C-GULL.
6. Only then mark #28 satisfied and remove the runtime dependency.

Passing CI on this branch demonstrates the #38 atomic-completion contract; it does
**not** by itself make the overall replacement gate ready.

## Reproduce the evidence

```bash
python -m pip install -e ".[dev]"
python -m pytest -q \
  tests/test_downstream_compatibility.py \
  tests/test_pcpp_differential.py \
  tests/test_cgull_migration_profile.py
python -m pytest -q
```

The repository CI runs the full suite across the supported Python/platform matrix.
The compatibility manifests are cross-checked so a supported configuration cannot
silently disappear from the differential gate, while explicit non-complete cases
remain structured, deterministic, and reviewable.