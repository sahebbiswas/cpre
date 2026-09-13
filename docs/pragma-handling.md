# Standard pragma syntax and host-owned semantics

cpre supports the **standard syntax and dispatch boundary** for reachable pragmas without implementing compiler- or target-specific pragma meaning.

`preprocess_source(..., pragma_handler=...)` routes both of these forms through the same host callback:

- active source `#pragma ...` directives;
- standard `_Pragma("...")` operators after ordinary macro expansion exposes them, including object-like and function-like macro expansion.

The callback receives a frozen `Pragma` value with:

- `payload`: the active `#pragma` preprocessing-token spelling, or the standard destringized `_Pragma` string payload;
- `origin`: `PragmaOrigin.DIRECTIVE` or `PragmaOrigin.OPERATOR`;
- `location`: the physical source location of the directive or `_Pragma` invocation;
- `filename`: the caller-supplied source identity, when present.

A handler returns `PragmaDisposition.CONSUME` only when the host's analysis profile can safely account for that pragma. Consumed pragmas are masked from canonical output while physical provenance remains stable. Returning `PragmaDisposition.UNSUPPORTED` or `None` produces the normal atomic `unsupported_preprocessing_directive` result. With no handler, any reachable pragma is likewise incomplete rather than silently ignored. Pragmas in discarded conditional branches are never dispatched.

```python
from cpre import PragmaDisposition, preprocess_source


def pragmas(pragma):
    if pragma.payload == "GCC diagnostic ignored \"-Wunused\"":
        # This decision belongs to the integrating analyzer, not cpre core.
        return PragmaDisposition.CONSUME
    return PragmaDisposition.UNSUPPORTED


result = preprocess_source(source, pragma_handler=pragmas)
```

## `_Pragma` destringization

cpre recognizes `_Pragma(string-literal)` after normal macro expansion. It removes the string-literal encoding prefix when present, removes the surrounding quotes, and destringizes `\"` to `"` and `\\` to `\` before dispatch. Malformed reachable `_Pragma` syntax returns a structured atomic incomplete result. Because `_Pragma` is a standard phase-4 preprocessing operator, a surviving `_Pragma` token that does not form the required parenthesized string-literal expression is treated as malformed reserved preprocessing syntax rather than passed through as an ordinary identifier.

Macro-generated forms such as this are supported:

```c
#define DO_PRAGMA(x) _Pragma(#x)
DO_PRAGMA(cpre analyzer_hint)
```

The reported location maps back to the macro invocation that exposed `_Pragma`, so host diagnostics remain deterministic and source-oriented.

## Deliberate boundary

cpre does **not** interpret GCC, Clang, MSVC, OpenMP, OpenACC, packing/alignment, section, optimization, diagnostic push/pop, `#pragma once`, or other vendor/toolchain pragma semantics. A downstream adapter may recognize whichever payloads are safe for its own analysis profile and reject everything else.

This is an analyzer extension point, not a compiler-compatibility claim. The conservative default is intentional: implementation-defined pragma meaning can change parsing, layout, diagnostics, code generation, or analyzer state, so cpre never guesses that an unknown pragma is harmless.
