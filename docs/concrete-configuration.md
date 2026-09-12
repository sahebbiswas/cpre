# Concrete macro configuration

`preprocess_source()` has two deliberately separate configuration inputs:

- `assumptions=` is the existing open-world Boolean model. It constrains branch reasoning but does **not** supply macro replacement text.
- `configuration=` is the concrete preprocessing model. It seeds actual external macro definitions/undefined state before the first source line and may opt into closed-world unknown-name handling.

Do not pass both. Keeping the two contracts separate prevents a Boolean assumption such as `FEATURE=True` from being silently reinterpreted as replacement text such as `1`.

## Configuration forms

Use `MacroConfiguration` for concrete caller-owned preprocessing state:

```python
import cpre

config = cpre.MacroConfiguration(
    presence={"HAVE_PLATFORM_API"},
    undefined={"DISABLED_FEATURE"},
    integers={
        "FEATURE_VALUE": 42,
        "ZERO_FEATURE": 0,
    },
    definitions=(
        cpre.MacroDefinition(
            "offsetof",
            "((size_t)&(((TYPE*)0)->MEMBER))",
            parameters=("TYPE", "MEMBER"),
        ),
    ),
    unknown_names=cpre.UnknownNamePolicy.UNDEFINED,
)

result = cpre.preprocess_source(source, configuration=config)
```

The forms are intentionally explicit:

- `presence={"NAME"}` creates an empty object-like definition, equivalent to `#define NAME` for definedness/presence checks.
- `undefined={"NAME"}` records an explicitly absent macro.
- `integers={"NAME": 42}` creates an object-like definition whose replacement text is that integer.
- `definitions=(MacroDefinition(...),)` supplies arbitrary object-like or function-like replacement text. External definitions have `location=None` because they do not originate in the physical source file.

Names may appear in only one configured category. Invalid names, duplicate/conflicting definitions, non-integer values, and invalid unknown-name policies raise `AnalysisError` with `ErrorCode.INVALID_CONFIGURATION`.

## Open versus closed unknown names

`UnknownNamePolicy.OPEN` is the default. A name omitted from configuration remains unknown when it controls a reachable branch. This preserves cpre's existing symbolic/open-world behavior.

`UnknownNamePolicy.UNDEFINED` is opt-in. It matches the relevant C-preprocessor/pcpp behavior for a concrete translation-unit configuration: an otherwise-unmentioned name is not defined, and an identifier that remains in a `#if` expression after macro expansion evaluates as `0`.

Closed-world handling is restricted to conditional evaluation. An unknown identifier in ordinary retained C/C++ source is **not** rewritten to `0`.

```python
source = "#if EXTERNAL_FEATURE\nint enabled;\n#endif\nint value = EXTERNAL_FEATURE;\n"
config = cpre.MacroConfiguration(
    unknown_names=cpre.UnknownNamePolicy.UNDEFINED,
)
result = cpre.preprocess_source(source, configuration=config)

assert result.complete
assert "int enabled" not in result.source
assert "EXTERNAL_FEATURE" in result.source  # ordinary source remains ordinary source
```

## Source-order ownership and mappings

Configured definitions seed the macro environment *before* line 1. Active source directives then own state in normal source order:

1. a source `#define` replaces the external configured definition;
2. a source `#undef` makes the macro undefined for following lines;
3. directives in discarded branches do not change state.

External replacement text does not create synthetic source lines. Expanded output continues to map to the physical invocation through `PreprocessResult.source_map`; configured `MacroDefinition.location` is `None`. The final `result.macros` snapshot contains explicitly configured/source-managed state, not every implicit closed-world name encountered during evaluation.

As with every concrete preprocessing result, consume `result.source` only when `result.complete` is true. Boolean `assumptions=` still do not provide replacement text; an assumed macro used where replacement text is required remains an incomplete `unsupported_macro_expansion` result rather than being guessed as `0` or `1`.

## C-GULL / pcpp migration ownership

This interface resolves the configuration-policy portion of the measured [pcpp replacement readiness gate](pcpp-readiness.md) tracked by [cpre issue #28](https://github.com/sahebbiswas/cpre/issues/28) and follow-up [#37](https://github.com/sahebbiswas/cpre/issues/37).

The ownership boundary is intentional:

- **cpre owns** the concrete configuration model, source-order override semantics, closed/open unknown-name policy, macro expansion, incomplete-result contract, and source mappings.
- **C-GULL owns** translating its build/runtime configuration into `MacroConfiguration`: which presence flags, explicit undefined names, integer values, and replacement definitions (including its `offsetof` definition) apply to a scan.
- C-GULL must explicitly choose `UnknownNamePolicy.UNDEFINED` when it wants pcpp-compatible closed-world behavior. cpre does not change its symbolic defaults globally.

This issue does not modify C-GULL. A downstream migration must still validate the remaining readiness items in #28, including built-ins/directives and the agreed input-scope policy, before removing pcpp.
