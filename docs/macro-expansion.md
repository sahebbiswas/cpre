# Concrete macro expansion

`preprocess_source` expands active ordinary-source macros using C/C++ preprocessing-token semantics. Macro definitions are applied in source order, expansion is bounded by the shared `AnalysisOptions.max_work` budget, and expanded output retains invocation provenance through `PreprocessResult.source_map`.

## Function-like substitution

Function-like macro arguments are collected before expansion. Ordinary parameter occurrences use a fully prescanned argument and the replacement list is then rescanned. Parameters used by stringification (`#`, or digraph `%:`) or adjacent to token pasting (`##`, or digraph `%:%:`) instead use the unexpanded argument tokens, as required by preprocessing semantics.

```c
#define X 7
#define STR(x) #x
#define XSTR(x) STR(x)
#define CAT(a,b) a ## b
#define X7 9

STR(X)      /* -> "X" */
XSTR(X)     /* -> "7" */
CAT(X, 7)   /* -> X7 -> 9 */
```

Stringification removes leading/trailing preprocessing whitespace, collapses intervening whitespace/comments to a single space, and escapes backslashes and double quotes so the result is a valid string-literal token.

Token pasting joins the boundary preprocessing tokens, then retokenizes the result. The paste must produce exactly one preprocessing token; otherwise preprocessing returns an atomic incomplete result with `ErrorCode.UNSUPPORTED_MACRO_EXPANSION` and an `invalid token paste` diagnostic. Empty macro arguments act as placemarkers, so pasting a token with an empty argument preserves the non-empty token.

## Variadic macros

Standard trailing `...` parameters are exposed through `__VA_ARGS__` and may participate in ordinary substitution, stringification, and token pasting.

```c
#define CALL(...) call(__VA_ARGS__)
#define SHOW(...) #__VA_ARGS__
#define SUFFIX(prefix, ...) prefix ## __VA_ARGS__

CALL()             /* -> call() */
CALL(a, b)         /* -> call(a,b) */
SHOW(a,b)          /* -> "a,b" */
SUFFIX(foo, bar)   /* -> foobar */
SUFFIX(foo, )      /* -> foo */
```

A variadic-only macro accepts an empty variadic argument with `V()`. For a macro with named parameters plus `...`, cpre currently requires the separating comma when the variadic portion is empty, for example `V(x,)`; omitted variadic arguments such as `V(x)` remain unsupported. `__VA_OPT__`, GNU named variadic parameters, and GNU comma-swallowing `, ## __VA_ARGS__` semantics are not implemented.

## Failure and mapping contract

Malformed `#` usage, invalid `##` placement, invalid token pastes, unsupported `__VA_OPT__`, arity errors, and resource-limit exhaustion never expose partial expanded source. Check `PreprocessResult.complete` before consuming `source`, `macros`, or `source_map`.

All tokens produced by stringification or token pasting map to the complete physical invocation range, consistent with other generated macro-expansion text. Nested expansions retain the outer source invocation provenance.
