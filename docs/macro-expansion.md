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

Token pasting joins the boundary preprocessing tokens, then retokenizes the result. The paste must produce exactly one preprocessing token; otherwise preprocessing returns an atomic incomplete result with `ErrorCode.UNSUPPORTED_MACRO_EXPANSION` and an `invalid token paste` diagnostic. Empty macro arguments act as placemarkers. Placemarkers are retained through chained paste operations and removed before rescanning, including when they originate inside `__VA_OPT__`.

## Variadic macros

Standard trailing `...` parameters are exposed through `__VA_ARGS__` and may participate in ordinary substitution, stringification, and token pasting. Standard `__VA_OPT__(tokens)` is supported inside variadic function-like replacement lists. Its contained tokens participate only when the hypothetical ordinary substitution of `__VA_ARGS__` contains preprocessing tokens after macro expansion; otherwise the construct behaves as an empty placemarker.

```c
#define CALL(...) call(__VA_ARGS__)
#define LOG(fmt, ...) log(fmt __VA_OPT__(,) __VA_ARGS__)
#define SHOW_OPT(...) #__VA_OPT__(__VA_ARGS__)
#define SUFFIX(...) pre ## __VA_OPT__(fix)
#define EMPTY

CALL()              /* -> call() */
CALL(a, b)          /* -> call(a,b) */
LOG("ok", )         /* -> log("ok") */
LOG("n=%d", n)     /* -> log("n=%d",n) */
SHOW_OPT()          /* -> "" */
SHOW_OPT(value)     /* -> "value" */
SUFFIX()            /* -> pre */
SUFFIX(x)           /* -> prefix */
LOG("empty", EMPTY) /* EMPTY expands away, so __VA_OPT__ is omitted */
```

`__VA_OPT__` content may contain nested parentheses, named parameters, `__VA_ARGS__`, stringification, and token pasting when those tokens form a valid replacement list for the current macro. The construct itself is treated like a parameter for surrounding `#`/`##` processing, so placemarkers are preserved long enough for standard-valid cases such as `#__VA_OPT__(...)` and `__VA_OPT__(a X ## X) ## b`.

A variadic-only macro accepts an empty variadic argument with `V()`. For a macro with named parameters plus `...`, cpre continues to require the separating comma when the variadic portion is empty, for example `V(x,)`; omitted variadic arguments such as `V(x)` remain outside the current compatibility contract. GNU named variadic parameters (`args...`) and GNU comma-swallowing `, ## __VA_ARGS__` semantics are not implemented.

`__VA_OPT__` is reserved for this variadic replacement-list role. Uses in ordinary source, object-like macros, non-variadic function-like macros, missing or unbalanced parentheses, nested `__VA_OPT__`, invalid replacement-list operators, and invalid paste results return an atomic incomplete preprocessing result rather than partial transformed source.

## Failure and mapping contract

Malformed `#` usage, invalid `##` placement, invalid token pastes, malformed/context-invalid `__VA_OPT__`, arity errors, and resource-limit exhaustion never expose partial expanded source. Check `PreprocessResult.complete` before consuming `source`, `macros`, or `source_map`.

All tokens produced by `__VA_OPT__`, stringification, or token pasting map to the complete physical invocation range, consistent with other generated macro-expansion text. Nested expansions retain the outer source invocation provenance.