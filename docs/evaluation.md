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

The same evaluation is available from the shell as [`cpre evaluate`](cli.md#macro-and-expression-values-with-evaluate).

## Semantics

Evaluation uses the same evaluator as concrete `#if` and `#elif` conditions, so a value is exactly what a condition would see. The grammar, the 64-bit `intmax_t`/`uintmax_t` model, the signed and unsigned conversions, C's truncating division and remainder, and the bounds are those of [Concrete `#if` / `#elif` expression support](conditional-expressions.md). It is the preprocessor's integer constant expression, not general C: there are no types beyond integers, so `sizeof`, casts, floating-point constants and string literals have no value.

Macros are expanded first, as in a condition: object-like macros, function-like macro calls, variadics and token pasting. `defined NAME` is resolved against the configuration. Names that the configuration marks undefined, or that its `unknown_names=UnknownNamePolicy.UNDEFINED` policy leaves undefined, evaluate as `0` inside an expression, as they do in `#if`. Under the default open-world policy an unconfigured name has no value, and the result is [symbolic](#symbolic-results).

A value that cannot be proved is never guessed. A signed result outside `[-2^63, 2^63 - 1]` is reported as overflow even though GCC and Clang warn and continue, and a division or remainder by zero is reported rather than raised.

## Results

Both functions return an immutable `Evaluation`:

| Field | Meaning |
| --- | --- |
| `kind` | `"expression"` or `"macro"` |
| `expression` | The text evaluated; the macro's name for a macro |
| `state` | `EvaluationState.CONCRETE`, `EvaluationState.SYMBOLIC` or `EvaluationState.UNEVALUABLE` |
| `value` | The integer, for a concrete result; otherwise `None` |
| `unsigned` | `True` when the value is a `uintmax_t`, `False` when it is an `intmax_t`; `None` without a value |
| `expanded` | The expression after macro expansion, whenever expansion got that far. For a symbolic result it is the result |
| `reason` | An `EvaluationReason`, for a symbolic or unevaluable result; otherwise `None` |
| `message` | A human-readable explanation, for a symbolic or unevaluable result; its wording is not a contract |
| `offset` | Offset in `expression` of the token the reason concerns, when one is known. A token that came from a macro's replacement is located at the macro that produced it |
| `unresolved` | The names that keep a symbolic result from having a value, in order of appearance; empty for the other states |

`Evaluation.concrete` and `Evaluation.symbolic` are shortcuts for the matching `state`, and `Evaluation.to_dict()` returns the record described in the [JSON contract](json-contract.md#cpreevaluation1).

### Symbolic results

Under the default open-world policy a name the configuration does not settle has no value, so `BASE + 4 * 2` cannot be computed. It is not an error either: the result is `symbolic`, and it shows what is known, the expression with every settled macro substituted and the unresolved names left exactly where they were.

```python
config = cpre.MacroConfiguration(
    integers={"WIDTH": 3},
    definitions=[cpre.MacroDefinition("AREA", "(WIDTH * 2 + PAD)")],
)

result = cpre.evaluate_expression("AREA % 5", config)
result.state  # EvaluationState.SYMBOLIC
result.expanded  # "(3 * 2 + PAD) % 5"
result.unresolved  # ("PAD",)
result.value  # None
```

A result is symbolic only when an unresolved name is the *only* obstacle:

- The expansion is never folded or regrouped. Nothing is computed from the settled parts, so the text is exactly what the compiler would see once `PAD` is defined, and an unparenthesized replacement still shows its precedence problem.
- Every other obstacle wins. With an unresolved name present, a division or remainder by a divisor that is certainly zero (`UNKNOWN + 1 / 0`), a signed overflow of settled operands, a shift count that is certainly out of range, unsupported tokens, an implementation-defined character constant and the nesting bound are all still `unevaluable`, with their own reason. An obstacle that depends on the unresolved name does not count: `1 / UNKNOWN` fails only if the name is zero, and `UNKNOWN && 1 / 0` only if it is not, so those stay symbolic.
- A C keyword is never an unresolved macro. `sizeof(int)`, `_Alignof(long)`, `_Generic`, the type keywords (`int`, `unsigned`, `struct`, ...) and any other C keyword, including inside the arguments of an unconfigured call such as `F(sizeof(int))`, make the result `unevaluable` with `unsupported_syntax`, whatever the configuration defines.
- A call of a name that is not a macro, such as `F(1, 2)`, is one unresolved name. Its arguments are macro-expanded and scanned for further unresolved names but not evaluated.
- `defined NAME` whose definedness the configuration does not settle is an unresolved name too; the operator is kept in `expanded`.
- An unresolved name that is the whole of an operand C would not evaluate (`0 && UNKNOWN`, `1 || UNKNOWN`, `1 ? 2 : UNKNOWN`) does not matter, so those have a concrete value. A name in an operand that is evaluated, or one that is only part of an unevaluated operand (`1 || UNKNOWN + 1`), still makes the result symbolic. See [Names in operands that are never evaluated](conditional-expressions.md#names-in-operands-that-are-never-evaluated).
- `unknown_names` decides what is unresolved. With `UnknownNamePolicy.UNDEFINED`, names evaluate as `0` and nothing is symbolic; names in `undefined=[...]` always evaluate as `0`. `evaluate_macro()` of a macro the configuration has neither defined nor undefined is symbolic with the macro itself as the unresolved name, while one that is explicitly undefined has nothing to evaluate and is `unevaluable`. The macro-analysis option `--symbolic-literal` concerns a different analysis and does not apply here.

`unknown_macros()` finds the names a whole source depends on; for one expression the result's own `unresolved` list is the discovery, taken from the expanded tokens, so names that only appear through macro replacement are included.

## Explaining a value

A value alone does not show why a result differs from what you expected. The usual causes are precedence (an unparenthesized replacement used inside a larger expression) and a conversion to unsigned. Pass `trace=True` to either function, or `--explain` to [`cpre evaluate`](cli.md#macro-and-expression-values-with-evaluate), to get the derivation in `Evaluation.trace`:

```python
config = cpre.MacroConfiguration(
    definitions=[
        cpre.MacroDefinition("W", "3"),
        cpre.MacroDefinition("H", "4"),
        cpre.MacroDefinition("SUM", "W + H"),
    ]
)

result = cpre.evaluate_expression("SUM * 2", config, trace=True)
for step in result.trace.steps:
    print(step.kind.value, step.text)
```

```text
start SUM * 2
expand SUM
warning SUM
expand W
expand H
expanded 3 + 4 * 2
evaluate 4 * 2
evaluate 3 + 4 * 2
result SUM * 2
```

`Evaluation.trace` is a `Trace` with `steps` (a tuple of `TraceStep`) and `truncated`. The value and every other field are the same with and without `trace=True`; a trace is `None` unless requested.

| `TraceKind` | Meaning |
| --- | --- |
| `START` | The expression, or the macro name, being evaluated. Always first |
| `EXPAND` | One macro replacement, in the order they happen: `text` is the invocation (`SQ(W + 1)`) and `replacement` what it became before rescanning. Macros inside arguments expand first |
| `WARNING` | The replacement just expanded regroups with its neighbours, with the reason in `note` |
| `EXPANDED` | The fully expanded expression, which is what is evaluated |
| `CONVERSION` | A signed operand became unsigned under the usual arithmetic conversions, with the value it became in `note` |
| `EVALUATE` | One operation that was evaluated, innermost first, with its `state`, `value` and `unsigned`. An operand that C does not evaluate (`1 / 0` after `0 &&`) has no step |
| `RESULT` | The value of a concrete result. Always last for one |
| `STOP` | Where evaluation ended without a value: `state` is `symbolic` or `unevaluable`, `replacement` is the expansion when there is one, and `note` gives the reason, the message and the offset. Always last for such a result |

Only `EVALUATE`, `RESULT` and `STOP` have a `state`, because they are the steps with an outcome; an `EVALUATE` inside a symbolic expression is `symbolic` and one that depends on nothing unresolved is `concrete`, so the settled parts of `3 * 4 + PAD` show as `3 * 4 = 12`.

### Precedence warnings

A `WARNING` is added when an expansion's top-level operator binds looser than the operator next to it, so its operands regroup. That covers a prefix operator (`-SUM`, `!SUM`), a tighter operator on either side (`2 * SUM`, `SUM % 4`), a non-associative operator of the same precedence on its left (`10 - DIFF` with `DIFF` as `H - W`) and a replacement that divides or takes a remainder next to a `*` (`2 * Q` with `Q` as `H / W`: `2 * 7 / 2` is 7, not `2 * (7 / 2)` = 6). It does not fire for a parenthesized replacement, for `1 + SUM` or `SUM - 1`, which group as they read, or for a replacement with no top-level operator. The test is the one `simplify-macros` uses for `&&` and `||` (a replacement with a top-level binary operator is not substitution-safe), extended to the arithmetic operators. A function-like macro's arguments are not checked against their parameters: `SQ_RAW(W + 1)` is shown through its `EXPANDED` text, `3 + 1 * 3 + 1`.

### Conversions

`CONVERSION` marks the step that is most often a surprise: `-1 < 0u` compares `18446744073709551615` with `0`, so it is `0`. It is added for the arithmetic, bitwise, relational and equality operators and for the arms of a conditional operator, and not for shifts, `&&`, `||` or `!`, which do not convert their operands to a common type. A conversion that keeps the value (`5 + 1u`) is noted as unchanged.

### Size

A trace is deterministic and holds at most 200 steps, so its size does not grow with the expression. When more steps occur, the later ones are dropped, `Trace.truncated` is `True` and the outcome step is still kept last. The work of producing it is part of the evaluation, within the same `max_work` limit; a result that is cut short by that limit ends in a `STOP` step.

### Reasons

An unevaluable result is an ordinary return value. Invalid arguments raise `AnalysisError`; nothing that merely prevents a value does. A symbolic result has the reason `UNRESOLVED_IDENTIFIER`.

| `EvaluationReason` | Example | Meaning |
| --- | --- | --- |
| `DIVISION_BY_ZERO` | `1 % (2 - 2)` | A selected `/` or `%` has a zero divisor |
| `OVERFLOW` | `9223372036854775807 + 1` | A signed result, constant or shift count is outside the model |
| `UNRESOLVED_IDENTIFIER` | `UNKNOWN + 1` | A name the configuration does not settle. The result is symbolic, or unevaluable for a macro that is explicitly not defined |
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
- A macro that depends on a name the configuration does not settle has no value; the result is [symbolic](#symbolic-results). [`unknown_macros()`](api.md#unknown-macro-dependencies) lists the names a source depends on.
- Character constants follow the rules in [Character constants](conditional-expressions.md#character-constants): `'a'` has no value because it depends on the execution character set.
