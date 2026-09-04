# Downstream compatibility contract

This document defines the supported `cpre` surface for library consumers such as static analyzers. Consumers should import from the top-level `cpre` package only and should not depend on `cpre.cpre`, ROBDD internals, private helpers, CLI rendering, or incidental expression formatting beyond the documented replacement strings.

## Supported contract

The compatibility suite in `tests/test_downstream_contract.py` is the executable contract for downstream integrations. It covers:

- documented top-level imports
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

## Compatibility and versioning

During the current `0.x` phase, `cpre` treats the documented public API as compatibility-sensitive even though semantic versioning traditionally permits breaking changes before `1.0`.

- Patch releases must preserve documented imports and established result semantics. They may fix incorrect behavior without deliberately reshaping the public contract.
- Minor releases may add backward-compatible public fields, types, finding categories, or capabilities. Downstream consumers should still review new finding kinds if they use exhaustive matching.
- Any deliberate incompatible change to the documented downstream contract requires an explicitly announced compatibility break and a minor-version boundary while `cpre` remains `0.x`.
- After `1.0`, intentionally breaking public API changes require a major version bump.

Downstream projects should pin a compatible release range and rely only on behavior covered by the public documentation and contract suite.

## Installed-package validation

CI builds the wheel, installs that wheel, copies the downstream contract test outside the repository checkout, and runs it against the installed package. CI also verifies `python -m cpre --help`. This catches missing package files, incorrect exports, and other packaging-only failures that editable-source tests can miss.
