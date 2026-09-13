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

`IncludeQuery` exposes the macro-expanded header spelling, whether the source used quoted or angle-bracket form, the physical source location of `__has_include`, and the caller-supplied filename. The callback must return `True`, `False`, or `None`. `None` means the host cannot answer and produces an atomic incomplete `PreprocessResult` with `ErrorCode.UNRESOLVED_CONDITION`; cpre never guesses `False`.

The operand is macro-expanded using the active source-order macro state before cpre interprets it as a header name. Both of these forms are supported:

```c
#define OPTIONAL_HEADER <optional.h>
#if __has_include(OPTIONAL_HEADER)
#endif

#define LOCAL_HEADER "local/config.h"
#if __has_include(LOCAL_HEADER)
#endif
```

Queries are lazy. A `__has_include` in an unreachable `#elif`, nested inactive branch, or Boolean term whose value is already irrelevant is not sent to the host callback.

## Boundary

This feature does **not** make `#include`, `#include_next`, or `#import` generally supported by concrete preprocessing. Active include directives remain owned by the host or prepared-translation-unit integration boundary.

cpre also does not:

- probe the filesystem by default;
- interpret compiler `-I`, `-isystem`, sysroot, framework, or builtin-header search rules;
- invoke a compiler;
- implement `__has_include_next`;
- implement vendor probes such as `__has_builtin`, `__has_attribute`, or `__has_feature`.

For quoted-header semantics that depend on the including file, use `IncludeQuery.filename` in the host callback. The callback is responsible for any search policy; cpre only preserves and reports the query form and spelling.
