# Standard pragma syntax and host-owned semantics

cpre supports the **standard syntax and dispatch boundary** for reachable pragmas without implementing compiler- or target-specific pragma meaning.

`preprocess_source(..., pragma_handler=...)` routes both of these forms through the same host callback:

- active source `#pragma ...` directives;
- standard `_Pragma("...")` operators after ordinary macro expansion exposes them, including object-like and function-like macro expansion.

The callback receives a frozen `Pragma` value with:

- `payload`: stable phase-3 pragma payload spelling after comment removal and whitespace normalization, for both source `#pragma` and destringized `_Pragma`;
- `origin`: `PragmaOrigin.DIRECTIVE` or `PragmaOrigin.OPERATOR`;
- `location`: the physical source location of the directive or `_Pragma` invocation;
- `filename`: the caller-supplied source identity, when present.

A handler returns `PragmaDisposition.CONSUME` only when the host's analysis profile can safely account for that pragma. Consumed pragmas are masked from canonical output while physical provenance remains stable. Returning `PragmaDisposition.UNSUPPORTED` or `None` produces the normal atomic `unsupported_preprocessing_directive` result. With no handler, any reachable pragma is likewise incomplete rather than silently ignored. Pragmas in discarded conditional branches are never dispatched.

```python
from cpre import PragmaDisposition, preprocess_source


def pragmas(pragma):
    if pragma.payload == 'GCC diagnostic ignored "-Wunused"':
        # This decision belongs to the integrating analyzer, not cpre core.
        return PragmaDisposition.CONSUME
    return PragmaDisposition.UNSUPPORTED


result = preprocess_source(source, pragma_handler=pragmas)
```

## Command line

`cpre preprocess` and `cpre cover` have no pragma callback, so they take the policy as an option. `--consume-pragma PREFIX` (repeatable) installs a handler that returns `PragmaDisposition.CONSUME` for a pragma whose payload starts with the preprocessing tokens of `PREFIX`, and `PragmaDisposition.UNSUPPORTED` for every other pragma:

```bash
cpre preprocess header.h -D FEATURE --consume-pragma once --consume-pragma pack
cpre cover header.h --consume-pragma once
```

- Matching is by whole tokens after the same normalization the callback sees: `pack` matches `pack(push, 1)` but not `packed`, and `GCC diagnostic` matches `GCC diagnostic ignored "-Wunused"` whatever the spacing. `_Pragma` operators match on their destringized payload.
- Nothing is consumed unless asked for. Without the option, a reachable pragma is incomplete exactly as before (status `2`, no output), and the text diagnostic is followed by a `hint:` line naming the option. With the option, a pragma that matches no prefix is reported as `reachable pragma is unsupported by the caller's pragma handler: PAYLOAD`, with a hint such as `add --consume-pragma pack to accept this pragma`.
- Accepting a pragma means only that cpre may drop it from the output. cpre still attaches no meaning to it, so accept the ones that cannot change which conditions are true (`once`, `pack`, diagnostics, `region`) and leave alone any whose effect you need to model.
- The same policy applies to pragmas in resolved includes. `#pragma once` in an included header is always honored as include management and needs no option.
- An empty prefix, or one that is only whitespace or a comment, is a usage error (status `2`).

## `_Pragma` destringization

cpre recognizes `_Pragma(string-literal)` after normal macro expansion. It removes the string-literal encoding prefix when present, removes the surrounding quotes, and destringizes `\"` to `"` and `\\` to `\`. The resulting character sequence is then normalized with the same phase-3 comment/whitespace handling used for source `#pragma`, so the host sees one consistent payload representation. Malformed reachable `_Pragma` syntax returns a structured atomic incomplete result. Because `_Pragma` is a standard phase-4 preprocessing operator, a surviving `_Pragma` token that does not form the required parenthesized string-literal expression is treated as malformed reserved preprocessing syntax rather than passed through as an ordinary identifier.

Macro-generated forms such as this are supported:

```c
#define DO_PRAGMA(x) _Pragma(#x)
DO_PRAGMA(cpre analyzer_hint)
```

The reported location maps back to the macro invocation that exposed `_Pragma`, so host diagnostics remain deterministic and source-oriented.

## Deliberate boundary

cpre does **not** interpret GCC, Clang, MSVC, OpenMP, OpenACC, packing/alignment, section, optimization, diagnostic push/pop, `#pragma once`, or other vendor/toolchain pragma semantics. A downstream adapter may recognize whichever payloads are safe for its own analysis profile and reject everything else.

This is an analyzer extension point, not a compiler-compatibility claim. The conservative default is intentional: implementation-defined pragma meaning can change parsing, layout, diagnostics, code generation, or analyzer state, so cpre never guesses that an unknown pragma is harmless.
