# Macro Boolean simplification analysis

This guide describes `cpre`'s bounded ROBDD Boolean reasoning for object-like `#define` replacement lists, introduced in issue #78.

For conditional control-directive analysis (e.g., `#if`, `#elif`), see the [Python API integration guide](api.md) and [Exact Boolean queries](boolean-queries.md).

## Overview

Many C and C++ projects encode feature flags, build configuration relationships, and component toggles in object-like macros:

```c
#define FEAT_1 (A || (!A && B))
#define FEAT_2 ((A && B) || (A && !B))
#define FEAT_3 (!!A)
```

`cpre` applies bounded Reduced Ordered Binary Decision Diagram (ROBDD) analysis to recognize eligible Boolean macro definitions, prove semantic equivalence, and report simplified replacement lists:

- `FEAT_1` simplifies to `(A || B)`
- `FEAT_2` simplifies to `(A)`
- `FEAT_3` simplifies to `(A)`

Simplification is proof-oriented: every rewrite is verified for exact semantic equivalence under the ROBDD model rather than relying on an ad-hoc set of syntactic rewrite rules.

## Supported macro-expression subset

To prevent accidental rewriting of non-Boolean C constructs, the candidate classifier is intentionally **conservative**:

### Candidate requirements

A macro definition is recognized as a candidate for Boolean simplification if and only if all of the following conditions hold:

1. **Object-like only**: The macro must not have a parameter list (`parameters is None`). Function-like macros (e.g., `#define API(x) ((x) + 1)`) are excluded.
2. **Non-empty replacement**: Empty `#define` statements (e.g., `#define FEATURE_PRESENT`) are excluded.
3. **Supported Boolean grammar**: The replacement list must consist strictly of:
   - Identifiers (`[A-Za-z_]\w*`), which are treated as Boolean atoms;
   - Boolean integer literals: only `0` (false) and `1` (true);
   - Boolean operators: logical AND (`&&`), logical OR (`||`), and logical NOT (`!`);
   - Grouping parentheses: `(` and `)`.
4. **No unmodeled C operators or constructs**: The replacement list must not contain:
   - Arithmetic operators (`+`, `-`, `*`, `/`, `%`);
   - Bitwise or shift operators (`&`, `|`, `^`, `~`, `<<`, `>>`);
   - Comparison operators (`==`, `!=`, `<`, `<=`, `>`, `>=`);
   - Conditional ternary operators (`? :`);
   - Comma operator (`,`);
   - String or character literals (`"..."`, `'...'`);
   - Non-Boolean integer literals (e.g., `1024`, `0x8000`, `2`, `42`);
   - Function calls or macro invocations (e.g., `foo(...)`);
   - The preprocessor `defined(...)` operator (which is undefined in `#define` replacement lists per the C standard).
5. **Presence of Boolean operators**: The replacement list must contain at least one Boolean operator (`&&`, `||`, `!`). Bare identifiers (such as `#define ALIAS OTHER`) and bare constants (such as `#define TIMEOUT 0`) are not treated as Boolean expressions.

### Parenthesization and formatting

If the original replacement list was enclosed in outer parentheses, the simplified replacement preserves outer parentheses. For example:

- `(A || (!A && B))` -> `(A || B)`
- `A || (!A && B)` -> `A || B`
- `(!!A)` -> `(A)`
- `!!A` -> `A`

## Semantic limits

The analyzer maintains clear semantic boundaries:

- **Distinction from general constant folding**: `cpre` simplifies Boolean expressions, not general C integer expressions. Integer constants `0` and `1` maintain their normal Boolean truth values (`0` = false, `1` = true). Ordinary integer constants such as `1024` or `0x8000` are never reinterpreted as truth-values.
- **Local and non-recursive**: Analyzing a replacement list does not expand other macro definitions. Identifiers in the replacement list remain symbolic atoms even if prior lines define them to integer values.
- **Deterministic resource bounds**: ROBDD construction respects `AnalysisOptions` resource limits (`max_atoms`, `max_bdd_nodes`, `max_work`). If a resource limit is exceeded, analysis is marked incomplete and no unproven rewrite is emitted.

## Python API

The macro analysis capability is exposed via two functions and a structured result type:

```python
import cpre

# Analyze a single macro definition
definition = cpre.MacroDefinition("FEAT_1", "(A || (!A && B))")
result = cpre.analyze_macro(definition)

assert result.candidate is True
assert result.simplified is True
assert result.simplified_replacement == "(A || B)"
assert result.is_equivalent is True

# Analyze all macros in a C source string
source = """
#define FEAT_1 (A || (!A && B))
#define BUFFER_SIZE 1024
#define FEAT_2 ((A && B) || (A && !B))
"""

for macro_result in cpre.analyze_macros(source):
    if macro_result.simplified:
        print(
            f"{macro_result.name}: {macro_result.original_replacement} -> {macro_result.simplified_replacement}"
        )
    elif not macro_result.candidate:
        print(f"{macro_result.name} skipped: {macro_result.reason}")
```

### `MacroAnalysisResult`

Each analyzed macro produces a structured `MacroAnalysisResult`:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Macro identifier |
| `definition` | `MacroDefinition` | Source definition metadata |
| `candidate` | `bool` | `True` if recognized as a supported Boolean macro |
| `reason` | `str \| None` | Diagnostic reason when skipped or unsimplified |
| `original_replacement` | `str` | Original replacement text |
| `original_expression` | `Expression \| None` | Parsed Boolean AST (if candidate) |
| `simplified_expression` | `Expression \| None` | Simplified Boolean AST (if candidate) |
| `simplified_replacement` | `str \| None` | Formatted simplified replacement (if simplified) |
| `is_equivalent` | `bool \| None` | ROBDD proof-of-equivalence status |
| `incomplete` | `AnalysisIncomplete \| None` | Resource limit diagnostic (if limits exceeded) |
| `replacement_range` | `SourceRange \| None` | Exact 1-based source range of the replacement text |
| `simplified` | `bool` (property) | `True` if a simpler equivalent expression was found |
| `complete` | `bool` (property) | `True` if analysis completed within resource limits |
| `location` | `SourceLocation \| None` (property) | Source location of the `#define` directive |

The `replacement_range` provides exact source coordinates suitable for downstream automated reporting and safe source rewriting workflows.
