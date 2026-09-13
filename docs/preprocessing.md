# Concrete preprocessing

`cpre.preprocess_source()` selects one concrete conditional configuration and expands the supported preprocessing surface without invoking a compiler or reading headers itself. Use it when a downstream parser or analyzer needs a single selected source representation rather than symbolic findings about all possible configurations.

For symbolic dead/redundant-branch analysis, use [`analyze_source()`](api.md) instead.

## Basic selection

```python
import cpre

source = """\
#ifdef FEATURE
int enabled;
#else
int disabled;
#endif
"""

result = cpre.preprocess_source(
    source,
    filename="example.c",
    assumptions={"FEATURE": True},
)

if not result.complete:
    for diagnostic in result.incomplete:
        print(diagnostic.code, diagnostic.location, diagnostic.message)
else:
    selected_source = result.source
```

`preprocess_source()` accepts source text. It does not open files, discover a compiler, or infer a build environment from the host machine.

## When to use `analyze_source()` vs `preprocess_source()`

Use `analyze_source()` when you want cpre to reason symbolically about conditional structure and report dead, redundant, or simplifiable branches across possible configurations.

Use `preprocess_source()` when you want cpre to choose one configuration and produce source for a downstream consumer. Concrete preprocessing can use explicit external macro definitions, source-order `#define`/`#undef` updates, bounded macro expansion, deterministic predefined-macro context, host-assisted `__has_include`, and host-owned pragma handling.

The two APIs deliberately have different outputs and failure contracts. Do not treat concrete preprocessing as a replacement for symbolic analysis when you need configuration-independent findings.

## `PreprocessResult`

A completed result exposes:

- `source`: the canonical coordinate/source-map-oriented preprocessed text;
- `filename`: the source identity supplied by the caller;
- `complete`: `True` when the requested preprocessing operation completed exactly within the documented surface;
- `incomplete`: ordered structured diagnostics when preprocessing cannot complete;
- `macros`: a detached, read-only snapshot of the final explicitly tracked macro state;
- `source_map`: mappings from canonical output spans back to physical input provenance;
- `removed_lines`: an immutable set of one-based physical lines wholly masked by preprocessing and eligible for opt-in compaction.

Always check `complete` before consuming transformed state:

```python
result = cpre.preprocess_source(source, configuration=config)
if not result.complete:
    handle_incomplete(result.incomplete)
    return

consume(result.source, result.source_map)
```

### Atomic incomplete results

Concrete preprocessing is atomic. When a reachable construct cannot be processed under the documented contract, cpre returns an incomplete `PreprocessResult` instead of exposing a partly transformed translation unit.

For incomplete results, `source` is `None`; transformation-derived state such as `macros`, `source_map`, and `removed_lines` is not available. Resource-limit exhaustion follows the same rule. Malformed conditional structure may instead raise the public structured `CpreError`/`ParseError` hierarchy.

An incomplete result is not equivalent to a successful result with empty output.

## Canonical output and coordinate guarantees

`result.source` is the default and canonical representation. cpre masks inactive text and handled preprocessing directives with spaces while preserving physical line endings. This keeps physical line relationships stable for downstream analyzers.

Macro expansion can change text length and output columns, so `result.source_map` is the authoritative bridge from canonical output back to physical source. Unexpanded spans map one-to-one; generated macro-expansion text maps to the physical invocation provenance defined by the macro-expansion contract.

Block comments that overlap retained text are restored as needed so retained output remains lexically balanced. Removed-line provenance is computed from the final canonical representation, after such restoration.

Coordinate-sensitive parsers and analyzers should normally consume `result.source` plus `result.source_map`, not the compact representation described below.

## Canonical vs compact output

Compaction is **strictly opt-in**. Calling `preprocess_source()` never removes preprocessing-created lines automatically.

```python
import cpre

result = cpre.preprocess_source(source, assumptions={"FEATURE": True})
if not result.complete:
    raise RuntimeError(result.incomplete)

canonical = result.source
compact_source = cpre.compact(result)
```

Successful results expose `removed_lines`, a `frozenset` of one-based physical line numbers that preprocessing wholly masked. `compact()` acts only on those lines; it never guesses from whitespace and never modifies retained lines, including intentionally blank retained lines.

```python
compact_source = cpre.compact(
    result,
    max_consecutive_blank_lines=1,
)
```

`max_consecutive_blank_lines=0` removes preprocessing-created blank lines entirely. Positive values retain up to that many consecutive removed lines as blank lines while preserving their original newline spelling. A retained line, including an intentional blank line, breaks a removed-line run.

Negative limits raise `ValueError`. Passing an incomplete result also raises `ValueError`.

### Mapping boundary for compact output

`source_map` describes **only** the canonical `result.source`. `compact()` does not rewrite, reinterpret, or synthesize a mapping for compact output.

Compaction is therefore a presentation/serialization view, not a formatting step and not a different preprocessing mode. It does not re-indent code, strip retained whitespace, prune comments, or alter macro-expanded retained text.

## Conditional selection semantics

Concrete preprocessing supports nested standard conditional groups including:

```text
#if
#ifdef
#ifndef
#elif
#elifdef
#elifndef
#else
#endif
```

Boolean assumptions may be supplied as a mapping or `MacroAssumptions`. Unmentioned names remain open-world/unknown unless the caller instead selects a concrete `MacroConfiguration` policy.

If a reachable branch condition cannot be resolved after the supported bounded Boolean/numeric/macro-expansion reasoning, the result is incomplete rather than guessed.

For the concrete integer-expression grammar and deterministic implementation-defined boundaries, see [Concrete conditional expressions](conditional-expressions.md).

## External macro configuration

For build-oriented external macro definitions, prefer `MacroConfiguration` over Boolean `assumptions=`:

```python
config = cpre.MacroConfiguration(
    presence={"FEATURE"},
    integers={"FEATURE_LEVEL": 2},
    unknown_names=cpre.UnknownNamePolicy.UNDEFINED,
)

result = cpre.preprocess_source(source, configuration=config)
```

`assumptions=` and `configuration=` are mutually exclusive so Boolean constraints cannot silently conflict with concrete replacement definitions.

`UnknownNamePolicy.UNDEFINED` is explicit opt-in behavior for callers that want otherwise-unmentioned names to behave as undefined/zero during concrete evaluation. The default remains open-world.

See [Concrete macro configuration](concrete-configuration.md) for presence, integer values, explicit undefined names, arbitrary macro definitions, and unknown-name policy.

## Macro state and source-order updates

Active `#define` and `#undef` directives update the per-run macro environment in source order and are masked in canonical output. Directives in discarded branches do not affect state.

On success, `result.macros` contains the final tracked `MacroState` values. Source definitions override externally configured state from the point at which the directive is reached; earlier conditions are never reevaluated after a later redefinition.

Object-like and function-like macro expansion, argument prescan/rescan, stringification, token pasting, standard variadics, `__VA_ARGS__`, and supported `__VA_OPT__` behavior are documented in [Macro expansion](macro-expansion.md). That document is the authoritative detailed expansion reference.

## Deterministic predefined-macro context

cpre does not discover host compiler predefined macros or wall-clock values. Callers provide supported deterministic environment values with `PreprocessingContext`:

```python
context = cpre.PreprocessingContext(
    standard_macros={
        "__STDC__": "1",
        "__STDC_VERSION__": "202311L",
        "__STDC_HOSTED__": "1",
        "__DATE__": '"Sep 12 2026"',
        "__TIME__": '"20:14:00"',
    }
)

result = cpre.preprocess_source(
    source,
    filename="src/example.c",
    configuration=config,
    context=context,
)
```

`__LINE__` and `__FILE__` come from logical preprocessing state rather than caller-supplied context. Standard `#line` directives update logical line/file state for subsequent preprocessing while diagnostics and source mappings continue to use physical input coordinates.

Environment-dependent predefined-macro value uses that lack deterministic configured semantics remain incomplete rather than being fabricated from the machine running cpre.

## Host-assisted `__has_include`

`preprocess_source(..., include_query=...)` supports standard `__has_include(...)` condition queries without implementing compiler header search.

The host callback answers the already-parsed query. cpre owns condition evaluation and macro expansion; the host owns filesystem/toolchain availability policy. An unanswered query returns an incomplete result rather than being treated as false.

Active `#include`, `#include_next`, and `#import` directives are still outside the concrete transformation contract.

See [Host-assisted `__has_include`](has-include.md) for query objects, return values, laziness, and the header-search boundary.

## Host-owned pragma semantics

Reachable standard `#pragma` and `_Pragma("...")` syntax can be routed through `pragma_handler=`. cpre recognizes and normalizes the standard syntax but intentionally does not implement vendor/compiler pragma meaning.

```python
import cpre


def handle_pragma(pragma):
    if pragma.payload == "cpre analyzer_hint":
        return cpre.PragmaDisposition.CONSUME
    return cpre.PragmaDisposition.UNSUPPORTED

result = cpre.preprocess_source(source, pragma_handler=handle_pragma)
```

A consumed pragma is masked from canonical output. Unsupported/unknown reachable pragmas preserve the conservative atomic-incomplete contract. Pragmas in discarded branches are not dispatched.

See [Pragma handling](pragma-handling.md) for `Pragma`, origins, destringization, and handler semantics.

## Supported and unsupported directive boundary

The concrete preprocessor handles conditional directives, source-order macro definitions/undefinitions, standard supported `#line`, and the documented host extension points above.

A null `#` directive is harmless and masked. Reachable directives outside the supported/host-accounted surface return structured incomplete diagnostics rather than being silently ignored. This includes active include directives and implementation-specific preprocessing behavior not covered by an explicit cpre contract.

The exact compatibility boundary is intentionally conservative: `complete=True` certifies the documented cpre operations for the provided deterministic inputs. It does **not** claim general GCC/Clang/MSVC/pcpp equivalence.

See [Downstream compatibility](downstream-compatibility.md) and [pcpp replacement readiness](pcpp-readiness.md) for integration and migration boundaries.

## Resource limits

Concrete preprocessing uses the same `AnalysisOptions` resource-limit model as symbolic analysis:

```python
options = cpre.AnalysisOptions(
    max_atoms=64,
    max_bdd_nodes=100_000,
    max_work=500_000,
)
result = cpre.preprocess_source(source, configuration=config, options=options)
```

When a deterministic limit is exhausted, preprocessing returns an incomplete result and exposes no partial transformed source.

## Downstream integration checklist

For coordinate-sensitive downstream analyzers:

1. Read source and own filesystem/build policy in the host tool.
2. Supply explicit configuration/context rather than relying on host inference.
3. Call `preprocess_source()` once for the source/configuration being modeled.
4. Check `result.complete` before consuming any transformed output.
5. Use canonical `result.source` for parsing/analysis and `result.source_map` for physical provenance.
6. Use `compact()` only for display/persistence when coordinate mapping is not required.
7. Treat include availability and pragma meaning as host-owned policies through their explicit callbacks.
8. Propagate incomplete diagnostics rather than converting them into a clean result.
