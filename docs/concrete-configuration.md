# Concrete macro configuration

`preprocess_source()` has three deliberately distinct configuration inputs:

- `assumptions=` is the existing open-world Boolean model. It constrains branch reasoning but does **not** supply macro replacement text.
- `configuration=` is the concrete preprocessing model. It seeds actual external macro definitions/undefined state before the first source line and may opt into closed-world unknown-name handling.
- `context=` supplies deterministic values for supported **standard predefined macros** whose values come from the preprocessing environment rather than source `#define` directives.

Do not pass `assumptions=` and `configuration=` together. Keeping those two contracts separate prevents a Boolean assumption such as `FEATURE=True` from being silently reinterpreted as replacement text such as `1`. `context=` may accompany either form, but it cannot redefine a standard predefined name already supplied by that macro state.

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

## Standard predefined preprocessing context

Use `PreprocessingContext` for deterministic standard-environment values. Replacement text is explicit and reproducible; cpre does not read the wall clock, inspect the host compiler, infer an ABI, or guess a language mode.

```python
context = cpre.PreprocessingContext(
    standard_macros={
        "__STDC__": "1",
        "__STDC_VERSION__": "202311L",
        "__STDC_HOSTED__": "1",
        "__DATE__": '"Sep 12 2026"',
        "__TIME__": '"20:14:00"',
    }
)

result = cpre.preprocess_source(
    source,
    filename="src/example.c",
    configuration=config,
    context=context,
)
```

The context accepts the standard names cpre explicitly models, including applicable `__STDC*` names, `__DATE__`, `__TIME__`, and `__cplusplus`. Values are exact preprocessing replacement text. Location-sensitive `__LINE__` and `__FILE__` are intentionally **not** configured through this mapping:

- `__LINE__` comes from the active logical preprocessing line;
- `__FILE__` comes from the supplied `filename=` or the active standard `#line` filename;
- `#line <integer>` changes the logical line assigned to the following source line;
- `#line <integer> "file"` also changes the logical file identity;
- operands of `#line` are macro-expanded before they are interpreted.

`#line` changes logical preprocessing state only. `SourceLocation`, `PreprocessResult.source_map`, diagnostics, and `result.filename` continue to identify the original physical input. This distinction is intentional for analyzers that must retain stable provenance while honoring portable source-level line remapping.

If a reachable environment-dependent standard predefined macro has no deterministic value, preprocessing remains atomic and returns `unsupported_macro_expansion`; no partial source or source map is exposed. In particular, cpre never fabricates `__DATE__`/`__TIME__` from the current clock. The same source plus the same configuration/context therefore produces byte-for-byte equivalent preprocessing output.

Vendor and target catalogs remain out of scope. `PreprocessingContext` does not accept names such as `__GNUC__`, `__clang__`, `_MSC_VER`, architecture/endianness/pointer-width macros, or compiler optimization/feature defines. It also does not discover a host compiler or import its predefined-macro dump.

## Open versus closed unknown names

`UnknownNamePolicy.OPEN` is the default. A name omitted from configuration remains unknown when it controls a reachable branch. This preserves cpre's existing symbolic/open-world behavior.

`UnknownNamePolicy.UNDEFINED` is opt-in. It matches the relevant C-preprocessor/pcpp behavior for a concrete translation-unit configuration: an otherwise-unmentioned name is not defined, and an identifier that remains in a `#if` expression after macro expansion evaluates as `0`.

Closed-world handling is restricted to conditional evaluation. An unknown identifier in ordinary retained C/C++ source is **not** rewritten to `0`. Standard location-sensitive predefined macros remain available even in closed-world mode because they are supplied by preprocessing context rather than by the ordinary unknown-name policy.

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

Configured definitions and explicit standard-environment values seed preprocessing *before* line 1. Active source directives then own ordinary macro state in normal source order:

1. a source `#define` replaces an external configured definition;
2. a source `#undef` makes the macro undefined for following lines;
3. directives in discarded branches do not change state;
4. active `#line` directives update only logical line/file context for following lines.

External replacement text does not create synthetic source lines. Expanded output continues to map to the physical invocation through `PreprocessResult.source_map`; configured `MacroDefinition.location` is `None`. The final `result.macros` snapshot contains explicitly configured/source-managed state, including caller-supplied standard environment definitions, but logical `__LINE__`/`__FILE__` state is intentionally represented through expansion rather than as a single final macro definition.

As with every concrete preprocessing result, consume `result.source` only when `result.complete` is true. Boolean `assumptions=` still do not provide replacement text; an assumed macro used where replacement text is required remains an incomplete `unsupported_macro_expansion` result rather than being guessed as `0` or `1`.

## C-GULL / pcpp migration ownership

This interface resolves the configuration-policy and deterministic standard-predefined portions of the measured [pcpp replacement readiness gate](pcpp-readiness.md) tracked by [cpre issue #28](https://github.com/sahebbiswas/cpre/issues/28) and follow-up [#37](https://github.com/sahebbiswas/cpre/issues/37).

The ownership boundary is intentional:

- **cpre owns** the concrete configuration model, source-order override semantics, closed/open unknown-name policy, supported standard predefined macros and `#line` state, macro expansion, incomplete-result contract, and source mappings.
- **C-GULL owns** translating its build/runtime configuration into `MacroConfiguration` and `PreprocessingContext`: which presence flags, explicit undefined names, integer values, replacement definitions, standard language-environment values, and deterministic build-date/time values apply to a scan.
- C-GULL must explicitly choose `UnknownNamePolicy.UNDEFINED` when it wants pcpp-compatible closed-world behavior. cpre does not change its symbolic defaults globally.

This issue does not modify C-GULL. A downstream migration must still validate the remaining readiness items in #28, including include/input-scope policy and any compiler-specific behavior intentionally kept outside cpre's standard analyzer-oriented contract, before removing pcpp.
