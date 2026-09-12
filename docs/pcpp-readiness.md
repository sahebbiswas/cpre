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
surface. **Issue #38 remains a release/migration blocker** because built-ins and
retained directives can still produce complete-but-semantically-different output.

## Measured corpus and configuration coverage

The versioned differential corpus contains **12 source fixtures and 13
fixture/configuration cases**: **8 supported**, **2 unsupported**, **1 incomplete**,
and **2 divergent**. Seven distinct source fixtures are supported.
`FEATURE=True` and `FEATURE=False` are both exercised for
`configured_conditional.c`; source-local definitions are part of the fixtures.

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
| `divergent_builtin_line.c` | None | Complete output retains `__LINE__`; blocker #38 |
| `divergent_pragma.c` | None | Complete output retains `#pragma once`; blocker #38 |

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
| [#38: built-ins and directives](https://github.com/sahebbiswas/cpre/issues/38) | **Blocking** | Establish supported semantics or explicit structured incomplete behavior for predefined macros and retained directives such as `#pragma`; parseability is not equivalence. |

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

## Public compatibility contract

Use the top-level `preprocess_source`, `PreprocessResult`, `MacroConfiguration`,
macro types, `SourceMapping`, and structured errors documented in the
[API guide](api.md#concrete-conditional-selection). Unknown reachable conditions and
detected unsupported expansion return incomplete results atomically; consumers must
not treat incomplete output as a clean preprocessing result.

For the C-GULL migration, the concrete boundary is defined in
[`cgull-migration-profile.md`](cgull-migration-profile.md) and enforced by
`tests/test_cgull_migration_profile.py` plus the machine-readable
`tests/compatibility/cgull-profile.json`. The profile pins the downstream revision,
include policy, configuration ownership, typedef-prelude ordering, and unsupported
construct inventory.

`complete=True` still certifies the supported selection and expansion operations,
not general compiler preprocessing equivalence. Until #38 is resolved, built-ins or
retained directives can still produce semantically divergent complete output.
Neither full GCC/Clang compatibility nor general header processing is promised.

## Release decision procedure

1. Keep the public API contract and installed-wheel contract checks passing.
2. Run the focused compatibility/profile tests and the full suite on the candidate
   commit and CI platform matrix.
3. Keep every supported fixture/configuration in the equivalence set. Every excluded
   case must retain a reviewable rationale, downstream impact evidence, and regression
   coverage; `KNOWN_DIFFERENCES` alone is not sufficient evidence.
4. Resolve #38 before marking #28 satisfied.
5. At the actual C-GULL migration commit, re-run the input-profile inventory. Confirm
   project headers still expand before cpre, unresolved include directives are masked
   with physical line preservation, concrete configuration is translated through the
   reviewed policy, the typedef prelude is injected after cpre, and `__VA_OPT__`
   remains absent or is separately supported.
6. Validate downstream security findings, source locations, diagnostics, and
   configuration profiles before removing pcpp from C-GULL.

Passing CI on this branch verifies #39's bounded exclusion evidence; it does **not**
make the overall replacement gate ready while #38 remains open.

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
silently disappear from the differential gate, while the C-GULL profile tests keep
the two reviewed #39 exclusions explicitly outside cpre's supported raw-input
surface.
