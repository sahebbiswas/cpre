# C-GULL pcpp replacement readiness report

**Verdict: BLOCKED — do not remove C-GULL's runtime pcpp dependency yet.**

This report accompanies cpre 0.10.5 and [readiness issue #28](https://github.com/sahebbiswas/cpre/issues/28).
The prerequisite issues #21–#27 were verified complete on 2026-09-12. The review
started from cpre `e2bbee5`; the downstream reference is
[C-GULL c61b275](https://github.com/sahebbiswas/cgull/tree/c61b27520624c074661afa0a615160944b31ab3d).
No C-GULL implementation changes are part of this gate.

## Measured corpus and configuration coverage

The versioned corpus contains **12 source fixtures and 13 fixture/configuration
cases**: **7 supported**, **2 unsupported**, **2 incomplete**, and **2 divergent**.
Six distinct source fixtures are supported. `FEATURE=True` and `FEATURE=False`
are both exercised for `configured_conditional.c`; every other fixture uses no
external assumptions. Source-local definitions are part of the fixtures.
This is a controlled representative corpus, not a scan of all C-GULL inputs.

| Fixture | Configuration | Classification / result |
| --- | --- | --- |
| `offsetof_container.c` | Source-local definitions | Supported; member-pointer subtraction and null-base member offset AST verified |
| `nested_conditionals.c` | Source-local flags | Supported; nested branch selection |
| `source_order.c` | Define, undefine, redefine | Supported; expansion follows source order |
| `multiline_nested_macros.c` | Source-local macros | Supported; nested expansion and continued definition |
| `general_macro_operators.c` | Source-local macros | Supported; `#`, `##`, rescanning |
| `configured_conditional.c` | `FEATURE=True`, `FEATURE=False` | Supported in both configurations |
| `unsupported_include.c` | None | Unsupported directive, line 1; input-scope blocker #39 |
| `unsupported_va_opt.c` | None | Unsupported expansion, line 2; input-scope blocker #39 |
| `incomplete_unknown_condition.c` | None | Unresolved condition, line 1; configuration blocker #37 |
| `incomplete_numeric_condition.c` | `FOO=3` defined in source | Unresolved condition, line 2; evaluation blocker #36 |
| `divergent_builtin_line.c` | None | Complete output retains `__LINE__`; semantic blocker #38 |
| `divergent_pragma.c` | None | Complete output retains `#pragma once`; directive blocker #38 |

For each supported case, the harness checks exact preprocessing-token equality,
original token line equality, parseability through `pycparser`, and deterministic
repeated output. Only whitespace, comments, and line-marker formatting are
normalized. cpre preserves physical lines; the oracle's compact output is mapped
through its `#line` directives. Column equivalence is not claimed: expanded spans
must use cpre's `SourceMapping` invocation ranges. Source-map tests also check
expansion ranges and atomic absence of output/maps/state on incomplete results.

The `offsetof` test checks the parsed return expression: cast to `struct item *`,
subtraction from `(char *)member`, and `(size_t)&(((struct item *)0)->value)`.
That preserves the recovery shape needed by downstream AST/layout analysis.
It does not prove that all C-GULL security findings or externally injected
definitions are equivalent; those still require downstream migration tests.

## Blocking differences and downstream impact

| Follow-up | Evidence | Required resolution |
| --- | --- | --- |
| [#36: numeric conditions](https://github.com/sahebbiswas/cpre/issues/36) | cpre cannot select `#if FOO > 2` with `FOO=3`; pcpp emits `int selected = 1;`. C-GULL already tests numeric conditions. | Bounded concrete expression evaluation with differential branch tests |
| [#37: configuration policy](https://github.com/sahebbiswas/cpre/issues/37) | pcpp selects an absent macro as zero; cpre reports unknown. C-GULL injects presence flags, integer values and `offsetof` replacement definitions. | Explicit concrete configuration/adapter contract with equivalent values, unknown-name policy and mappings |
| [#38: built-ins and directives](https://github.com/sahebbiswas/cpre/issues/38) | cpre returns `complete=True` for unexpanded `__LINE__` and retained `#pragma once`; pcpp expands/removes them. | Supported semantics or explicit structured unsupported diagnostics; never infer equivalence from parseability alone |
| [#39: unsupported-input scope](https://github.com/sahebbiswas/cpre/issues/39) | Includes and `__VA_OPT__` are excluded without demonstrated downstream non-impact. C-GULL passes through missing includes and later strips them. | Reviewed input profile and regression evidence for exclusions, or separately implemented capabilities |

These are tracked blockers, **not approved non-impact exceptions**. Include
resolution is not required merely to close this gate; a demonstrated caller policy
may suffice. Conversely, deleting include lines without evidence is not a valid
proof. The `__VA_OPT__` case measures cpre's diagnostic, not equivalence with pcpp
or a full compiler. The current corpus has zero unexplained mismatches among
supported cases, but six explicitly excluded fixtures remain migration blockers.

## Public compatibility contract

Use the top-level `preprocess_source`, `PreprocessResult`, macro types,
`SourceMapping`, and structured errors documented in the
[API guide](api.md#concrete-conditional-selection). Supported operations include
source-order branch selection, object/function macros, standard variadic
substitution, stringification and token pasting. Unknown reachable conditions and
detected unsupported expansion return incomplete results atomically. Malformed
input can raise `CpreError`; resource exhaustion returns bounded diagnostics.

`complete=True` currently certifies these operations, not general preprocessing
equivalence: other directives may survive and predefined macros are not
automatically provided. Those limitations are precisely why #38 blocks the gate.
Neither full GCC/Clang compatibility nor general header processing is promised.
`pcpp` and `pycparser` remain development/test dependencies only.

## Release decision procedure

1. Keep the public API contract and installed-wheel contract checks passing.
2. Run the commands below on the candidate commit and the CI platform matrix.
3. Keep every supported fixture/configuration in the equivalence set. Record every
   excluded case with an issue, rationale, and measured downstream impact.
4. Resolve #36–#39. To retain an unsupported case, replace its blocker with reviewed
   evidence of non-impact on the agreed C-GULL input profile and a regression test
   enforcing that boundary. A reason in `KNOWN_DIFFERENCES` alone is insufficient.
5. Update this report with the exact candidate, dependency versions, corpus counts,
   configurations, and results. Only mark #28 satisfied when all its requirements,
   including explicit failure handling and security-case coverage, are met.
6. A downstream migration PR may then cite #28 and this versioned report, pin the
   validated cpre release, and validate its own configuration injection, typedef
   prelude, include policy, source locations, diagnostics, and security findings.

Passing CI today verifies documented successes **and reproduces the blockers**.
It must not be interpreted as a ready verdict or an instruction to close #28.

## Reproduce the evidence

```bash
python -m pip install -e ".[dev]"
python -m pytest -q tests/test_downstream_compatibility.py tests/test_pcpp_differential.py
python -m pytest -q
```

The focused run for this report passed **45 tests**, and the full suite passed
**386 tests**, on Python 3.12.14, pcpp 1.30, pycparser 2.23 and pytest 9.1.1.
The repository CI runs the full suite on
Python 3.9–3.13 across Linux, macOS and Windows; local results do not replace those
checks. The classification and differential manifests are cross-checked so a
supported configuration cannot silently disappear from the oracle comparison.
