# Include resolution

`cpre.preprocess_source(..., include_resolver=...)` preprocesses reachable `#include`, `#include_next`, and `#import` directives recursively, without building any compiler or filesystem search policy into cpre.

The work is split between the caller and cpre:

- **The resolver (caller)** maps one include request to source text and a deterministic identity.
- **cpre** handles recursive preprocessing, shared macro state, cycle and depth checks, `#pragma once`, `#import`, include guards, provenance, and diagnostics.

Without a resolver, include behavior is unchanged: reachable includes return an incomplete result, or are masked when [`skip_includes=True`](preprocessing.md#opt-in-include-skipping) is set.

## In-memory resolver

```python
import cpre
from cpre.includes import IncludeRequest, ResolvedInclude

headers = {
    "config.h": "#ifndef CONFIG_H\n#define CONFIG_H\n#define FEATURE 1\n#endif\n",
}


def resolve(request: IncludeRequest) -> ResolvedInclude | None:
    text = headers.get(request.header)
    return None if text is None else ResolvedInclude(request.header, text)


result = cpre.preprocess_source(
    '#include "config.h"\n#if FEATURE\nint enabled;\n#endif\n',
    filename="main.c",
    include_resolver=resolve,
)
assert result.complete
```

`IncludeRequest` contains:

- `header`: the header spelling between the delimiters.
- `form`: `IncludeForm.QUOTED` or `IncludeForm.ANGLE`.
- `directive`: `"include"`, `"include_next"`, or `"import"`.
- `location`: the directive's physical location in the including source.
- `includer`: the including source's identity. This is the caller's `filename` for the primary source, and otherwise the `ResolvedInclude.identity` of an included source.
- `depth`: the includer's nesting depth, `0` for the primary source.

A resolver can implement `#include_next` and relative quoted includes from `directive` and `includer`.

The resolver returns a `ResolvedInclude(identity, source)` or `None`. `identity` must be the same every time for the same underlying source. cpre uses it for:

- cycle detection, `#pragma once`, `#import`, and include guards;
- `__FILE__` inside the included source;
- the `source_identity` field on diagnostics and source mappings.

The resolver can be called more than once for the same request (for example, when `__has_include` handling re-runs a prefix of the source), so it must be deterministic. Exceptions it raises propagate to the caller.

### Header names

`"header"` and `<header>` are read from the physical directive spelling, so characters such as `//` inside a header name are not treated as comments. Any other operand is macro-expanded with the current macro state and must produce a string literal or a `<`…`>` token sequence (a computed include). Text after the header name, an empty header name, or an operand that doesn't expand to a header name returns an incomplete result with `unsupported_preprocessing_directive`.

## Semantics

- **Shared macro state.** An included source starts with the macro state at the directive. Its `#define`/`#undef` directives affect later conditionals and expansion in the including source, exactly as source-order directives do. `result.macros` is the final state after all sources.
- **Output.** The directive line is masked as usual, and the included source's canonical output is inserted right after it. Each included source keeps its own line endings. If an included source doesn't end with a line ending, cpre adds `\n` so its last line can't join the next one, as compilers do at end of file.
- **Provenance.** `SourceMapping.source_identity` is `None` for spans from the primary source, and otherwise the identity of the included source that the span's offsets and locations refer to. Generated end-of-file newlines map to an empty range at the end of their source, with `expanded=True`. `result.includes` lists an `IncludeRecord(request, identity, outcome)` for each reachable resolved include, in processing order.
- **Coordinates.** When a source is entered, output lines no longer match the primary source's physical lines. Use `source_map` for provenance. `removed_lines` holds canonical *output* line numbers, which is what `compact()` uses. Without entered includes, output and physical lines still match.
- **`#pragma once`.** A reachable `#pragma once` in an included source stops that identity from being entered again (`IncludeOutcome.PRAGMA_ONCE`). cpre handles it itself; it isn't sent to `pragma_handler`. In the primary source, `#pragma once` is still a host pragma.
- **`#import`.** As in GCC and Clang, `#import` makes its source include-once: once a source has been entered and imported, later `#import` or `#include` directives of it are not re-entered (`IncludeOutcome.IMPORTED`).
- **Include guards.** cpre recognizes standard whole-file guards (`#ifndef X` / `#define X` … `#endif`) using the same detector as `MacroConfiguration.from_source()`. A guarded source whose guard macro is already defined is not re-entered (`IncludeOutcome.GUARDED`); that output would be empty anyway. Detected guard macros are file-private: when a guarded source is first entered and the guard's state is unknown, it starts undefined so the body is entered. A guard you configure explicitly is respected.
- **Other pragmas.** Reachable `#pragma` directives and `_Pragma` operators in included sources go to `pragma_handler` in output order, like pragmas in the primary source. `Pragma.filename` is the included source's identity and `Pragma.location` is within that source.

## Diagnostics

Failures return an atomic incomplete result. The `source_identity` field on diagnostics (`PreprocessDiagnostic`, `AnalysisIncomplete`) names the included source the location refers to, or is `None` for the primary source.

| Situation | Code |
| --- | --- |
| The resolver returns `None` and `skip_includes` is not set | `unresolved_include` |
| A source is re-entered while it is still being preprocessed, and isn't stopped by `#pragma once`, `#import`, or a defined guard | `include_cycle` |
| Nesting would exceed `max_include_depth` (default 200) | `include_depth_exceeded` |
| Malformed include operand | `unsupported_preprocessing_directive` |

Malformed conditionals in an included source raise `ParseError` with `filename` set to that source's identity. A resolver result that isn't a `ResolvedInclude`, or invalid `max_include_depth` / `include_resolver` arguments, raise `AnalysisError` with `invalid_configuration`.

Self-recursive includes that aren't guarded (an occasional preprocessor-metaprogramming technique) are reported as cycles rather than run until the depth limit.

### Combining with `skip_includes`

With both `include_resolver` and `skip_includes=True`, an include the resolver can't answer is masked and reported in `skipped_includes` (with `source_identity` set for directives inside included sources). Includes it does resolve are preprocessed normally. The [skip-mode semantic limitation](preprocessing.md#opt-in-include-skipping) applies to the skipped ones.

## Filesystem resolver

`cpre.includes.SearchPathResolver` is an optional resolver for explicit search directories:

```python
from cpre.includes import SearchPathResolver

resolver = SearchPathResolver(["include", "third_party/include"], quote_paths=["src"])
result = cpre.preprocess_source(text, filename="src/main.c", include_resolver=resolver)
```

It searches as follows:

- **Quoted includes:** the includer's directory, then `quote_paths`, then `search_paths`.
- **Angle-bracket includes:** `search_paths` only.
- **`#include_next`:** continues after the directory that contains the includer.
- **Absolute header names:** used as written.

Identities are normalized joined paths. Files are read as UTF-8 by default, with line endings preserved. Read failures raise `AnalysisError` with `source_read_error`. It never consults compiler default or system directories.

The CLI exposes it as `-I DIR`/`--include-dir DIR`, `--iquote DIR`, and `--max-include-depth N`. See the [CLI guide](cli.md#concrete-preprocessing).

## Current boundaries

- `__has_include` is answered in the primary source only. A `__has_include` condition inside an included source returns `unresolved_condition`.
- `__INCLUDE_LEVEL__` remains an unsupported predefined macro.
- Resolution is never inferred from the host machine; everything cpre reads comes from the resolver you supply.
