# Compact preprocessing output

Compact output is now documented as part of the authoritative [Concrete preprocessing](preprocessing.md#canonical-vs-compact-output) guide.

The compatibility contract remains unchanged: `preprocess_source()` always returns the canonical coordinate/source-map-oriented representation, and callers explicitly opt into `cpre.compact(result)` when they want preprocessing-created blank lines collapsed for presentation or persistence.

`PreprocessResult.source_map` describes canonical `result.source` only; no compact-output source map is implied or synthesized.
