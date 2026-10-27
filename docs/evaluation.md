# Evaluating expressions and macros

`evaluate_expression()` and `evaluate_macro()` answer "what is the value of this?" for integer arithmetic: a macro such as `#define AREA (WIDTH * HEIGHT + PAD)`, or an expression that uses macros, under a concrete [`MacroConfiguration`](concrete-configuration.md). Without them the only way to learn a value is to write a probe `#if` and see which branch is taken.

```python
import cpre

config = cpre.MacroConfiguration(
    integers={"WIDTH": 3, "HEIGHT": 4, "PAD": 2},
    definitions=[cpre.MacroDefinition("AREA", "(WIDTH * HEIGHT + PAD)")],
)

result = cpre.evaluate_macro("AREA", config)
result.value  # 14
result.expanded  # "(3 * 4 + 2)"

cpre.evaluate_expression("AREA % 5", config).value  # 4
```

## Semantics

Evaluation uses the same evaluator as concrete `#if` and `#elif` conditions, so a value is exactly what a condition would see. The grammar, the 64-bit `intmax_t`/`uintmax_t` model, the signed and unsigned conversions, C's truncating division and remainder, and the bounds are those of [Concrete `#if` / `#elif` expression support](conditional-expressions.md). It is the preprocessor's integer constant expression, not general C: there are no types beyond integers, so `sizeof`, casts, floating-point constants and string literals have no value.

Macros are expanded first, as in a condition: object-like macros, function-like macro calls, variadics and token pasting. `defined NAME` is resolved against the configuration. Names that the configuration marks undefined, or that its `unknown_names=UnknownNamePolicy.UNDEFINED` policy leaves undefined, evaluate as `0` inside an expression, as they do in `#if`. Under the default open-world policy an unconfigured name has no value, and the result says so.

A value that cannot be proved is never guessed. A signed result outside `[-2^63, 2^63 - 1]` is reported as overflow even though GCC and Clang warn and continue, and a division or remainder by zero is reported rather than raised.

## Results

Both functions return an immutable `Evaluation`:

| Field | Meaning |
| --- | --- |
| `kind` | `"expression"` or `"macro"` |
| `expression` | The text evaluated; the macro's name for a macro |
| `state` | `EvaluationState.CONCRETE` or `EvaluationState.UNEVALUABLE` |
| `value` | The integer, for a concrete result; otherwise `None` |
| `unsigned` | `True` when the value is a `uintmax_t`, `False` when it is an `intmax_t`; `None` when unevaluable |
| `expanded` | The expression after macro expansion, whenever expansion got that far |
| `reason` | An `EvaluationReason`, for an unevaluable result; otherwise `None` |
| `message` | A human-readable explanation, for an unevaluable result; its wording is not a contract |
| `offset` | Offset in `expression` of the token the reason concerns, when one is known. A token that came from a macro's replacement is located at the macro that produced it |

`Evaluation.concrete` is a shortcut for `state is EvaluationState.CONCRETE`, and `Evaluation.to_dict()` returns the record described in the [JSON contract](json-contract.md#cpreevaluation1).

### Reasons

An unevaluable result is an ordinary return value. Invalid arguments raise `AnalysisError`; nothing that merely prevents a value does.

| `EvaluationReason` | Example | Meaning |
| --- | --- | --- |
| `DIVISION_BY_ZERO` | `1 % (2 - 2)` | A selected `/` or `%` has a zero divisor |
| `OVERFLOW` | `9223372036854775807 + 1` | A signed result, constant or shift count is outside the model |
| `UNRESOLVED_IDENTIFIER` | `UNKNOWN + 1` | A name the configuration does not settle. For `evaluate_macro`, also a macro that is not defined |
| `UNSUPPORTED_SYNTAX` | `1 +`, `1.5`, `"text"` | The text is not an integer constant expression, or an empty replacement list |
| `IMPLEMENTATION_DEFINED` | `'ab'` | The value depends on the compiler's character set or `char` |
| `FUNCTION_LIKE` | `evaluate_macro("SQUARE", ...)` | A bare function-like macro; evaluate a call of it as an expression |
| `NESTING_BOUND` | forty nested parentheses | The expression nests deeper than the evaluator's bound |
| `EXPANSION_ERROR` | `SQUARE(1, 2)`, `__LINE__` | Macro expansion failed, or reached a predefined macro cpre does not model |
| `BUDGET_EXCEEDED` | any, with a small `max_work` | The work limit of `AnalysisOptions` was reached |

## Precedence: evaluate the use, not just the macro

A macro's replacement text is substituted, not computed. `evaluate_macro()` gives the value of the name expanded on its own; it cannot know how the name will be used.

```python
config = cpre.MacroConfiguration(
    definitions=[
        cpre.MacroDefinition("A", "1"),
        cpre.MacroDefinition("B", "2"),
        cpre.MacroDefinition("SUM", "A + B"),
    ]
)

cpre.evaluate_macro("SUM", config).value  # 3
cpre.evaluate_expression("SUM * 2", config).value  # 5
cpre.evaluate_expression("SUM * 2", config).expanded  # "1 + 2 * 2"
cpre.evaluate_expression("(SUM) * 2", config).value  # 6
```

The `expanded` text is the check: if it is not the expression you meant, the macro needs parentheses. `simplify-macros` explains related rewrites in [Macro Boolean simplification](macro-simplification.md).

## Arguments

- `configuration`: a `MacroConfiguration`. Optional for `evaluate_expression` (no macros are defined); required for `evaluate_macro`.
- `context`: a [`PreprocessingContext`](concrete-configuration.md) supplying standard predefined macro values, such as `__STDC_HOSTED__`. It cannot redefine a name the configuration already sets.
- `options`: an `AnalysisOptions`; evaluation spends the shared `max_work` budget on macro expansion and parsing.

`evaluate_macro()` also requires `name` to be an identifier.

## Limits

- Only integer constant expressions are evaluated. Non-integer or non-constant C is `UNSUPPORTED_SYNTAX` or `UNRESOLVED_IDENTIFIER`.
- A macro that depends on a name the configuration does not settle has no value. [`unknown_macros()`](api.md#unknown-macro-dependencies) lists the names a source depends on.
- Character constants follow the rules in [Character constants](conditional-expressions.md#character-constants): `'a'` has no value because it depends on the execution character set.
