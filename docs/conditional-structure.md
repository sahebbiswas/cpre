# Lossless conditional structure API

Use `cpre.parse_conditionals()` when a downstream analyzer needs source-preserving preprocessor structure rather than branch selection or a Boolean-analysis verdict.

```python
import cpre

tree = cpre.parse_conditionals(source_text, filename="src/example.c")
```

This API is configuration-independent. It does not resolve includes, apply macro configuration, select active branches, or rewrite source. Recoverable structural problems are represented in `tree.diagnostics` while recoverable directives and blocks remain available.

## Which cpre API to use

cpre deliberately exposes three different operations:

- `parse_conditionals()` builds lossless conditional structure and physical source mappings. It is the API for refactoring tools, source-aware static analyzers, and consumers that need to retain malformed structure.
- `analyze_source()` performs symbolic reachability/redundancy/simplification analysis. Its historical `ConditionalTree` is the analyzer model and is distinct from `ConditionalStructureTree`.
- `preprocess_source()` performs concrete preprocessing under a supplied or inferred macro configuration and may select/discard branches.

Downstream code should not import `cpre.parser`, `cpre.model`, or other internal implementation modules to obtain structural information.

## Public structural model

`parse_conditionals()` returns a `ConditionalStructureTree` with:

- `source`: the exact original Python string;
- `filename`: optional caller-supplied source identity;
- `blocks`: top-level `ConditionalBlock` values in source order;
- `directives`: every recognized conditional-family directive in source order;
- `diagnostics`: recoverable `StructureDiagnostic` values in deterministic source order.

A `ConditionalBlock` contains ordered `ConditionalBranch` values, an optional closing `endif` directive, its complete block range, and an optional parent branch. A branch contains its opening/branch directive, body range, parent block, and nested child blocks.

A `ConditionalDirective` exposes:

- `kind`: `if`, `ifdef`, `ifndef`, `elif`, `elifdef`, `elifndef`, `else`, or `endif`;
- `source_range`: the full physical logical-directive range, including continuation lines;
- `condition_range`: the exact significant condition range when the directive accepts a condition;
- `condition_text`: the exact original physical substring for `condition_range`;
- `logical_condition`: the spliced/comment-masked logical condition used for structural symbolic extraction;
- `condition`: a public symbolic `Expression` when cpre can safely retain one, otherwise `None`;
- `tokens`: ordered `DirectiveToken` values with exact physical ranges.

C23 `#elifdef` and `#elifndef` are first-class directive kinds.

## Coordinate contract

Structural locations use `StructuralSourceLocation(offset, line, column)`:

- `offset` is the Python string offset into the exact original source;
- `line` is one-based;
- `column` is one-based.

`StructuralSourceRange` is half-open `[start, end)`. Every structural range carries offsets, so callers can slice without reparsing:

```python
span = branch.body_range
assert span.text(source_text) == source_text[span.start.offset:span.end.offset]
```

Backslash-newline splicing does not destroy physical provenance. A logical token or condition that crosses a splice may therefore have a physical range whose text includes the original backslash and newline. LF and CRLF input use the same one-based coordinate rules.

The structural coordinate types are intentionally separate from the existing `SourceLocation` / `SourceRange` used by analysis findings and suggested edits. This keeps the established analysis API backward compatible while making offset presence mandatory for the lossless structural API.

## Original and logical condition text

`condition_text` is for source mapping and editing. It is the exact original physical substring between the first and last significant condition token. Internal comments and continuation sequences remain present; trailing comments outside the significant condition range are excluded.

`logical_condition` is for interpretation. Backslash-newline pairs are spliced and comments are replaced as preprocessing whitespace before symbolic structure is extracted.

Do not use `logical_condition` offsets against the original source. Use `condition_range` for all physical slicing or edits.

## Recovery diagnostics

Recoverable structural failures are data, not exceptions. `StructureDiagnostic.code` is a stable `StructureDiagnosticCode` value and each diagnostic has an exact physical `source_range`.

The public categories are:

- `MISSING_CONDITION`;
- `MALFORMED_MACRO_DIRECTIVE`;
- `TRAILING_DIRECTIVE_TEXT`;
- `UNMATCHED_DIRECTIVE`;
- `DUPLICATE_ELSE`;
- `BRANCH_AFTER_ELSE`;
- `UNTERMINATED_CONDITIONAL`.

Examples such as an unmatched `#elif`, duplicate `#else`, an `#elif` after `#else`, unexpected tokens after `#endif`, or a block that reaches EOF without `#endif` retain every recognized directive in the flat `directives` sequence. Blocks that can be recovered remain traversable, and unterminated block/body ranges extend deterministically to EOF.

Fatal API misuse, such as passing a non-string source, raises `TypeError`. Source-structure errors that can be represented without discarding unrelated structure are diagnostics instead.

## Symbolic condition field

The `condition` field uses the public symbolic expression types exported by `cpre`, including `Variable`, `DefinedVariable`, `Predicate`, `Negation`, `Conjunction`, and `Disjunction`.

The structural parser extracts Boolean structure only where doing so is safe with C operator precedence. Value-bearing or unsupported C syntax is retained as an opaque `Predicate` rather than being reinterpreted. Invalid macro-only forms such as `#ifdef A B` receive a diagnostic and have no symbolic condition.

Consumers must not treat the presence of `condition` as proof that the original C expression is semantically valid for every compiler. Structural parsing is intentionally not a full C constant-expression validator.

## Compatibility contract

All structural types and `parse_conditionals()` are exported from top-level `cpre` and are covered by source-tree tests plus an installed-wheel contract. Downstream consumers may rely on:

- top-level import availability;
- offset/line/column coordinate semantics;
- half-open range semantics;
- source-order traversal of blocks, branches, directives, tokens, and diagnostics;
- retained physical source text across continuations;
- C23 conditional forms;
- stable diagnostic categories;
- configuration-independent behavior.

Patch releases preserve this documented structural contract. Intentional incompatible changes follow the versioning policy in [Downstream compatibility](downstream-compatibility.md).
