# Empty blocks and branches

cpre reports a conditional block that has no content, and `#elif`/`#else` branches at the end of a block that have none, because deleting them changes nothing:

```c
#if FLAG_A
#endif

#if FLAG_B
int x;
#else
#endif
```

```text
L1 EMPTY      #if FLAG_A
  empty  this block has no content; it can be deleted
L6 EMPTY      #else
  empty  the empty branch at line 6 ends the block; it can be deleted

Summary  1 empty block · 1 empty branch run
```

Both findings carry a suggested edit with `EXACT` confidence that deletes the lines (the replacement is the empty string). The first deletes the whole block, the second the `#else` line, which leaves `#if FLAG_B … #endif`.

## Empty blocks

A block is empty when **every** branch body is only blank lines, or only empty blocks separated by blank lines. All of these hold:

- **No comment anywhere in the block.** A comment inside it, or on any of its directive lines (`#if A // why`, `#endif // end A`), would be deleted with it, so the block is kept. This matches the rule for the other [block rewrites](mergeable-blocks.md).
- **Nothing but blank lines.** A `#define`, `#undef`, `#error`, `#warning`, `#pragma`, `#include` or `#line`, even alone, is content. So is any text. A header guard is never reported.
- **Reachable.** Every branch is reachable. A dead or redundant branch is reported as `DEAD` or `REDUNDANT` instead, and the same deletion fixes both, so cpre does not report the block twice.
- **Inert conditions.** No condition depends on `__LINE__` or `__COUNTER__` (directly or through a macro defined in the file), names built with `##`, or `_Pragma`. Deleting the block removes the evaluation of its conditions, which would change a counter or skip a pragma.
- **No later line test.** No condition after the block depends on `__LINE__`: deleting the lines moves it up.

A block that holds only empty blocks is one finding at the outermost block; its `related_locations` lists the opening line of each empty block inside it. They are not reported again, nor offered as a [collapsible nesting](collapsible-nesting.md) or a [merge](mergeable-blocks.md).

## Empty trailing branches

The `#elif` and `#else` branches at the end of a block can be dropped when each has no body, no comment on its directive line and an inert condition. Consecutive empty branches at the end are one finding, anchored at the first of them, with the others in `related_locations`.

Only branches **at the end** qualify. An empty branch in the middle still decides the outcome:

```c
#if A
x;
#elif B      // empty
#elif C
y;
#endif
```

If `B` is true nothing is kept. Without the empty branch, `C` would be tested and `y;` could be kept. So it is not reported, and neither is an empty branch followed by a branch with content. An empty first branch (`#if A` / `#else` with content) is not dropped either; negating the condition is a different rewrite.

If a branch in the run cannot go (a comment on its directive, or it is unreachable), the finding covers the longest run of branches that can go and is safe to delete (shorter runs are tried when a longer one is not); apply the edit and analyze again to see what is left. A run that is followed by kept branches is offered only if each kept condition is safe to evaluate in cases where it used to be skipped (an `#else`, or a well-formed, inert condition); `#elif B` / `#elif 1/0` is not reduced to `#elif 1/0`.

A branch is never offered when its condition could fail to evaluate: it is not a well-formed `#if` expression, it calls a function-like macro this file does not define, or it divides (`/`, `%`). Deleting it would turn a preprocessing error into success. The same holds for the conditions of an empty block.

A block whose branches are all empty is an empty block, not a run of empty branches.

An empty branch is not offered as a [join](joinable-branches.md) with another empty branch; the deletion covers it. A join elsewhere in the same block is still offered.

## Assumptions

Macro assumptions can only remove a finding: a branch that assumptions make dead or redundant is reported as such instead, and an empty block or branch is reported only if its branches (for an empty block, every branch of it and of the blocks inside) are also reachable in the analysis without the assumptions. `depends_on_assumptions` is `False`.

## Line numbers

Applying the edit deletes lines, so the lines after it move up. That changes `__LINE__` in the code that follows and the numbers in later compiler diagnostics. cpre treats the meaning of a rewrite as the preprocessed output apart from those numbers, so this alone does not stop a finding, but a condition after the edit that depends on `__LINE__` does, as for [mergeable blocks](mergeable-blocks.md#line-numbers). That check follows only macros defined in the same file.

## Output

**Text.** The block, or the first empty branch, gets an `EMPTY` badge (yellow) and an `empty` line. The `Summary` line counts `empty blocks` and `empty branch runs`.

**Exit status.** Both count for `--fail-on-findings`.

**JSON.** In `cpre.analysis/1`, the group has `suggestions: [{"kind": "empty_block", "lines": [...]}]` (the lines of nested empty blocks, `[]` when there are none) or `[{"kind": "empty_branch", "lines": [...]}]` (the lines of every empty trailing branch, the first included). See the [JSON contract](json-contract.md#cpreanalysis1-file-record).

**SARIF.** `CPRE009` (`empty-block`) and `CPRE010` (`empty-branch`), both at `warning` level, each with a `fix` that deletes the lines. See [SARIF](sarif.md).

**Python.** `FindingKind.EMPTY_BLOCK` and `FindingKind.EMPTY_BRANCH`. See [the API](api.md).
