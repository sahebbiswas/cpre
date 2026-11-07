# Collapsible nesting

cpre reports a conditional block whose only content is another conditional block, because the two can be one `#if` that tests both conditions:

```c
#if FLAG_A
#if FLAG_B
int x;
#endif
#endif
```

```text
L1 NEST       #if FLAG_A
  nest  the only content of this block is a nested block at line 2; they can be one #if testing the conditions together with &&

Summary  1 collapsible nesting
```

The finding carries a suggested edit with `EXACT` confidence that replaces the whole outer block:

```c
#if FLAG_A && FLAG_B
int x;
#endif
```

Unlike the other block findings, this one is a **style suggestion**. Many codebases nest on purpose so that each level reads on its own, so it is reported at SARIF `note` level and it does **not** make `--fail-on-findings` fail. It still appears in the report, the JSON and the `Summary` line.

## What counts as collapsible

A block and the block inside it are a pair when all of these hold:

- **Plain blocks.** Both are a single `#if`, `#ifdef` or `#ifndef` branch with no `#elif` or `#else`. Anything else in the outer branch (code, a directive, a second nested block, a comment) means no finding, and only blank lines may sit between the outer opening line and the inner block, and between the inner `#endif` and the outer `#endif`.
- **Comment-free lines the edit changes.** The outer opening line, the inner opening line and the inner `#endif` carry no comment, because the edit rewrites the first and deletes the others. A comment on the *outer* `#endif`, or inside the inner body, is kept as it is.
- **Reachable and already simple.** Both openers are reachable (a dead or redundant nested block is reported as `DEAD` or `REDUNDANT` instead), and neither condition has a simpler equivalent form, because [simplifying](cli.md) the condition first avoids copying a redundant test into the collapsed one.
- **Plain directive lines.** Each rewritten line is one physical line that starts with `#`: no line continuation, no digraph.
- **Inert conditions.** No condition depends on `__LINE__` or `__COUNTER__`, directly or through a macro defined in the file; none uses a name that a macro of the file builds with `##` (it could form one of them); and none expands to a `_Pragma`. The collapsed test would evaluate them on another line, and the inner one even when the outer test is false.
- **Well-formed inner conditions.** An inner block is skipped without reading its condition when the outer test is false, but `A && (X)` is always parsed. So each inner condition must be a well-formed expression and call only `defined`, `__has_*` operators or macros defined in the file; `#if FOO(1)` with no `FOO`, or `#if B C`, is not collapsed.
- **No later line test.** No condition after the chain depends on `__LINE__`: the edit moves it up.

A chain of three or more blocks, each holding only the next, is one finding for the outermost block; its `related_locations` lists every inner block. If a link in the middle has a sibling, the chain stops there.

Unlike merging, the body cannot interact with the conditions: the outer block has nothing in it but the inner block, so there is no code that could change a macro between the two tests.

## Edits can overlap

The edit replaces the whole outermost block, including the innermost body, and cpre still looks for findings inside that body. A `mergeable_blocks` finding for two `#if C` blocks inside the chain therefore has a range that lies inside the nesting edit. Do not apply every edit of one result at once: apply the outermost first and analyze again.

## The collapsed condition

The result is always an `#if`. `#ifdef A` becomes `defined(A)` and `#ifndef A` becomes `!defined(A)`. A test that is a bare name, a `defined` test or the negation of either is used as it is. Any other test is wrapped in parentheses, unless it already is one parenthesized group: `A && (B || C)`, `(A > 1) && B`. A bare name is also wrapped when a `#define` in the file gives it a replacement list that is not a single token or a parenthesized expression.

A macro defined in a header or on the command line is not followed. If such a macro expands to something with a top-level `||`, `X && Y` can group differently from the two tests it replaces. Read the edit before applying it when a condition names a macro you cannot see.

The innermost body keeps its text, including its indentation. cpre does not re-indent it one level out.

## Output

**Text.** The outermost block gets a `NEST` badge (yellow) and a `nest` line naming the inner blocks. The `Summary` line counts `collapsible nesting`s.

**Exit status.** A collapsible nesting does **not** count for `--fail-on-findings`.

**JSON.** In `cpre.analysis/1`, the outermost group has `suggestions: [{"kind": "collapsible_nesting", "lines": [2]}]`, listing the opening line of each inner block. See the [JSON contract](json-contract.md#cpreanalysis1-file-record).

**SARIF.** Rule `CPRE008` (`collapsible-nesting`, `note`) is reported at the outermost block's opening directive, with a `relatedLocations` entry for each inner block and a `fix` that applies the edit. See [SARIF output](sarif.md).

**Python.** `analyze_source()` returns one `Finding` per chain with `kind` `FindingKind.COLLAPSIBLE_NESTING`. Its `location` is the outermost block's opening directive, `related_locations` holds the inner blocks' opening directives, and `edit` is the rewrite (`FixConfidence.EXACT`). `depends_on_assumptions` is `False`: macro assumptions can only remove a finding.

## Line numbers

Applying the edit deletes lines, so the lines after it move up. That changes `__LINE__` in the code that follows and the line numbers in later compiler diagnostics. cpre treats the meaning of a rewrite as the preprocessed output apart from those numbers, so this alone does not stop a finding, but a project that depends on exact line numbers should review the diff. A condition after the edit that depends on `__LINE__` does stop it, as for [mergeable blocks](mergeable-blocks.md#line-numbers).
