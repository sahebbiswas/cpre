# Compact preprocessing output

`preprocess_source()` always returns cpre's canonical coordinate-preserving representation. Inactive conditional branches and handled directives remain present as whitespace so physical line relationships and `source_map` stay trustworthy.

For consumers that explicitly want a human-readable or persisted compact view, cpre exposes `compact()` as a separate opt-in transformation:

```python
from cpre import compact, preprocess_source

result = preprocess_source(source, assumptions={"FEATURE": True})
if result.complete:
    canonical = result.source
    compact_source = compact(result)
```

Calling `preprocess_source()` alone never compacts output.

## Removed-line provenance

Successful `PreprocessResult` values expose `removed_lines`, an immutable `frozenset` of 1-based physical source line numbers that preprocessing wholly masked. Incomplete results expose `removed_lines=None`, consistently with the atomic result contract for `source`, `macros`, and `source_map`.

Provenance reflects the final canonical representation after block-comment restoration. If retained text requires a block comment to be restored across a line, that line is not reported as removable when restored content remains on it.

## `compact()`

```python
compact(result, *, max_consecutive_blank_lines=0) -> str
```

The helper only acts on lines listed in `result.removed_lines`. Retained lines—including intentionally blank retained lines and macro-expanded retained text—are emitted exactly as represented in `result.source`.

`max_consecutive_blank_lines=0` removes preprocessing-created blank lines entirely. Positive values retain up to that many consecutive removed lines as blank lines while preserving their original newline spelling. A retained line, including an intentional blank line, breaks a removed-line run.

Negative limits raise `ValueError`. Passing an incomplete result also raises `ValueError`; compact output is never synthesized from partial preprocessing state.

## Mapping boundary

`result.source` remains the only canonical representation. `result.source_map` describes that canonical output only. `compact()` does not reinterpret, replace, or synthesize a source map for the compact view.

Compaction is therefore a presentation/serialization choice made by the caller, not a formatting step and not a change to preprocessing semantics. It does not re-indent source, strip retained whitespace, prune comments, or otherwise rewrite retained content.
