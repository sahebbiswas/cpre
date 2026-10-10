# Host-assisted `__has_include`

`cpre.preprocess_source()` supports the standard `__has_include(...)` operator in reachable `#if` and `#elif` expressions without implementing compiler header search.

The host supplies one deterministic availability callback through `include_query=`. cpre owns preprocessing-expression semantics and macro expansion of the operand; the callback only answers whether the resulting header spelling is available.

```python
import cpre
from cpre.include_queries import IncludeForm, IncludeQuery

available = {
    (IncludeForm.ANGLE, "optional.h"),
    (IncludeForm.QUOTED, "local/config.h"),
}


def has_include(query: IncludeQuery) -> bool | None:
    return (query.form, query.header) in available

result = cpre.preprocess_source(
    '#if __has_include(<optional.h>)\nint enabled;\n#endif\n',
    filename="feature.c",
    include_query=has_include,
)
assert result.complete
```

`IncludeQuery` exposes the macro-expanded header spelling, whether the source used quoted or angle-bracket form, the physical source location of `__has_include` in the source that contains it, and that source's identity in `filename`: the caller-supplied `filename` for the primary source, or the resolved identity of an included source. The callback must return `True`, `False`, or `None`. `None` means the host cannot answer and produces an atomic incomplete `PreprocessResult` with `ErrorCode.UNRESOLVED_CONDITION`; cpre never guesses `False`.

The operand is macro-expanded using the active source-order macro state before cpre interprets it as a header name. Both of these forms are supported:

```c
#define OPTIONAL_HEADER <optional.h>
#if __has_include(OPTIONAL_HEADER)
#endif

#define LOCAL_HEADER "local/config.h"
#if __has_include(LOCAL_HEADER)
#endif
```

Queries are lazy. A `__has_include` in an unreachable `#elif`, nested inactive branch, or Boolean term whose value is already irrelevant is not sent to the host callback. Queries are evaluated under every unknown-name policy: with `MacroConfiguration(unknown_names="undefined")`, `__has_include` is still sent to the callback (or reported as unresolved without one) rather than treated as an undefined name.

## Included sources

With [include resolution](include-resolution.md), `__has_include` conditions inside resolved headers are answered through the same `include_query` callback, with the same semantics. `IncludeQuery.filename` is the header's resolved identity and `location` is the position in that header, so a callback can implement quoted-header search relative to the including file. The operand is expanded with the macro state at that point in the run, so a header entered twice can ask different questions. An unanswered query in a header is an atomic incomplete result whose diagnostic has the header's `source_identity`.

```python
import cpre
from cpre.include_queries import IncludeQuery
from cpre.includes import IncludeRequest, ResolvedInclude

headers = {"config.h": "#if __has_include(<optional.h>)\n#define HAVE_OPTIONAL 1\n#endif\n"}


def resolve(request: IncludeRequest) -> ResolvedInclude | None:
    text = headers.get(request.header)
    return ResolvedInclude(request.header, text) if text is not None else None


def has_include(query: IncludeQuery) -> bool | None:
    return query.header == "optional.h"


result = cpre.preprocess_source(
    '#include "config.h"\n#if HAVE_OPTIONAL\nint enabled;\n#endif\n',
    include_resolver=resolve,
    include_query=has_include,
)
assert result.complete
```

## Answers from the include resolver

With `include_resolver=` you can also opt into `has_include_from_resolver=True`. A query that `include_query` does not answer, because there is no callback or because it returned `None`, then goes to the include resolver. The header is available exactly when the resolver returns a `ResolvedInclude` for the equivalent `#include` request, so `__has_include` and `#include` agree:

- The request has the query's header spelling and form, `directive="include"`, the query's location, `includer` set to the identity of the source containing the query, and that source's `depth`. Quoted-form queries therefore search relative to the containing source, as `#include` does.
- Probing does not include anything: the returned source is not preprocessed and the probe is not recorded in `includes`.
- An explicit `True` or `False` from `include_query` takes precedence and the resolver is not called; only `None` falls back.
- Queries stay lazy: the resolver is asked only for queries that decide a reachable condition.
- `defined(__has_include)` is true when either source of answers is enabled.

This is opt-in because it is sound only when the resolver has no side effects that matter for a probe. A resolver that, say, records or rewrites state on every call sees `__has_include` probes as ordinary requests. `has_include_from_resolver=True` without `include_resolver` raises `AnalysisError` with `invalid_configuration`.

```python
import cpre
from cpre.includes import IncludeRequest, ResolvedInclude

headers = {"optional.h": ""}


def resolve(request: IncludeRequest) -> ResolvedInclude | None:
    text = headers.get(request.header)
    return ResolvedInclude(request.header, text) if text is not None else None


result = cpre.preprocess_source(
    "#if __has_include(<optional.h>)\nint enabled;\n#endif\n",
    include_resolver=resolve,
    has_include_from_resolver=True,
)
assert result.complete
```

On the command line, `cpre preprocess --has-include-from-search` answers queries from the `-I`/`--iquote` search (see the [CLI guide](cli.md#concrete-preprocessing)).

## Feature tests

`defined(__has_include)`, `defined __has_include`, and `#ifdef __has_include` are true when `include_query` is given or `has_include_from_resolver=True`. Without a callback they are unknown, under either unknown-name policy, so cpre never guesses. `__has_include_next` is not implemented: `defined(__has_include_next)` is false, so the usual feature-test fallback is selected, and a reachable `__has_include_next(...)` that decides a condition is an `unsupported_condition_expression` incomplete result.

## Boundary

This feature does **not** resolve `#include`, `#include_next`, or `#import` directives; use [include resolution](include-resolution.md) to preprocess headers, or [include skipping](preprocessing.md#opt-in-include-skipping) to treat them as opaque. By default the callback answers queries; cpre derives availability from the include resolver only when you opt in with [`has_include_from_resolver=True`](#answers-from-the-include-resolver).

cpre also does not:

- probe the filesystem by default (the CLI's `--has-include-from-search` searches only the `-I`/`--iquote` directories you give it);
- interpret compiler `-I`, `-isystem`, sysroot, framework, or builtin-header search rules;
- invoke a compiler;
- implement `__has_include_next` (see [Feature tests](#feature-tests));
- implement vendor probes such as `__has_builtin`, `__has_attribute`, or `__has_feature`.

For quoted-header semantics that depend on the including file, use `IncludeQuery.filename` in the host callback. The callback is responsible for any search policy; cpre only preserves and reports the query form and spelling.
