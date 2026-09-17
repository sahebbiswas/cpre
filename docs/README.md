# cpre documentation

Use this index to choose the guide that matches your integration surface.

## Start here

- [Command-line interface](cli.md) — invocation, inputs/discovery, text/JSON/SARIF reporting, CI behavior, errors, and exit statuses.
- [Python API integration](api.md) — public symbolic expressions/algebra/interchange, `analyze_source()`, structured findings, simplifications, edits, errors, resource limits, and compatibility expectations.
- [Lossless conditional structure](conditional-structure.md) — `parse_conditionals()`, exact physical ranges, nested branches, recovery diagnostics, and source-preserving downstream integration.
- [Concrete preprocessing](preprocessing.md) — `preprocess_source()`, canonical output, source mappings, macro state, compact output, deterministic context, and downstream parser integration.

## Preprocessing references

- [Concrete macro configuration](concrete-configuration.md) — external macro definitions and unknown-name policy.
- [Concrete conditional expressions](conditional-expressions.md) — supported integer-expression grammar and deterministic boundaries.
- [Macro expansion](macro-expansion.md) — object/function-like expansion, prescan/rescan, variadics, stringification, token pasting, `__VA_OPT__`, mapping, and failures.
- [Host-assisted `__has_include`](has-include.md) — deterministic host-owned header-availability queries.
- [Pragma handling](pragma-handling.md) — standard pragma syntax with host-owned semantics.

## Integration and compatibility

- [SARIF output](sarif.md) — SARIF 2.1.0 rules, fixes, notifications, and code-scanning integration.
- [Downstream compatibility](downstream-compatibility.md) — supported transformation and API guarantees for consumers.
- [C-GULL migration profile](cgull-migration-profile.md) — cpre integration profile for C-GULL.
- [pcpp replacement readiness](pcpp-readiness.md) — measured compatibility gate and remaining migration boundaries.

The repository [README](../README.md) is intentionally the short release/PyPI landing page. Detailed behavior belongs in these guides so each contract has one authoritative home.
