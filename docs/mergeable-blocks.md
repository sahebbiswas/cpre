# Mergeable blocks

cpre reports two conditional blocks that test the same condition, sit next to each other, and could be one block. A condition that is written twice is usually a leftover from two changes that each added their own guard, and one block is easier to read and to keep in step.

```c
#if FLAG_A
int x;
#endif


#if FLAG_A
int y;
#endif
```

```bash
cpre source.c
```

```text
L1 MERGE      #if FLAG_A
  merge  the same condition is tested again at line 6; the blocks can be merged

Summary  1 mergeable block run
```

The suggested form is the first block with the later bodies appended:

```c
#if FLAG_A
int x;
int y;
#endif
```

The finding carries a suggested edit with `EXACT` confidence: it deletes the first block's `#endif`, the blank lines, and the later blocks' opening directives, and joins the bodies under the first block's `#if`. The blank lines between blocks go with the deleted directives, and cpre does not reformat the merged body.

## What counts as mergeable

A run is two or more sibling blocks (blocks in the same parent branch, or all at the top level) where every neighbouring pair meets all of these conditions:

- **Plain blocks.** Each is an `#if`, `#ifdef` or `#ifndef` with no `#elif`, `#elifdef`, `#elifndef` or `#else`. A block with another branch is not a simple concatenation, so it is never reported.
- **Only blank lines between.** The `#endif` of the first block is followed by the opening directive of the second with nothing but blank lines (spaces, tabs, form feeds and line breaks) in between. Any comment, code, or directive between them means the pair is not reported. So does a comment on that `#endif` line or on the second block's opening line: a merge would delete both directives and with them the comment. A comment on the *first* block's opening line, on the *last* block's `#endif`, or inside a body does not matter, because a merge keeps those.
- **Equivalent conditions.** The two conditions are Boolean-equivalent. `A && B` matches `B && A`, `!(A || B)` matches `!A && !B`, and `#ifdef A` matches `#if defined(A)`. A name's *value* and its *definedness* are different tests, so `#if A` does not match `#ifdef A`.
- **Only names and Boolean operators.** The conditions use names, `defined`, `!`, `&&` and `||`, and the names are only compared when the two conditions share at least one. A condition cpre treats as an opaque predicate (`A > 1`, `FOO(1)`, `__has_include(<x.h>)`) is not compared, and neither are `__LINE__` and `__COUNTER__`, whose value changes from one use to the next, a name that a macro of the file builds with `##` (it could form one of them), or a `_Pragma` that expanding the condition would run.
- **Reachable.** The opening branch of every block in the run is reachable. A dead or redundant block is reported as `DEAD` or `REDUNDANT` instead, and a block of a conditional that was [not analyzed](api.md#partial-results-and-incomplete-components) is never part of a run.
- **A body that cannot change the condition.** The body of every block but the last holds no `#include`, `#include_next`, `#import`, `#pragma` or digraph directive (`%:`), no `_Pragma` operator (written directly or reached through a macro defined in the file), and no `#define` or `#undef` of a name the condition uses or that those names expand to in this file. Nested blocks count as part of the body. A header or a `push_macro`/`pop_macro` pragma can change any macro, and a `#define` or `#undef` of a name in the condition could make the second test come out differently than the first, so merging would change what the program means. A `#define` of an unrelated macro is fine.

A run of three or more blocks is one finding that lists every later block. Adjacent blocks that test a condition and its *opposite* are a different finding; see [Complementary blocks](complementary-blocks.md).

### Macros defined in this file

cpre follows `#define`s in the same file. If a condition names `FEATURE`, the file defines `FEATURE` as `LEVEL`, and the first block holds `#undef LEVEL`, the pair is not reported. A condition that uses a name defined as `__LINE__` or `__COUNTER__` is not reported either, and neither is one whose equivalence depends on regrouping a function-like or unparenthesized definition (`#define A X || Y` makes `A && B` and `B && A` different tokens).

### What is not tracked

Macros defined in a header or on the command line are not followed, as in the rest of the analysis. If `FEATURE` is defined in a header as depending on `LEVEL` and the first block redefines `LEVEL`, cpre does not see the connection, so read the pair before accepting the edit when a body defines macros.

### Assumptions

Macro assumptions can only remove a finding, and a run is reported only if its blocks are also reachable in the analysis without assumptions. A run found with assumptions is also found without them, so `depends_on_assumptions` is always `False`; a block that assumptions make dead or redundant is reported as `DEAD` or `REDUNDANT` instead.

## Output

**Text.** The first block of a run gets a `MERGE` badge (yellow) and a `merge` line naming the lines where the following blocks begin. A run is counted once in the `Summary` line as a `mergeable block run`. If the first block is also simplifiable, it still shows `MERGE`, with the `-`/`+` lines under it.

**Exit status.** A mergeable run counts as a finding for `--fail-on-findings`, like a dead or redundant branch.

**JSON.** Every group in `cpre.analysis/1` has `merge_with`: the opening lines of the blocks that can be merged into it, in source order. It is `[]` for every block that is not the first of a run. See the [JSON contract](json-contract.md#cpreanalysis1-file-record).

**SARIF.** Rule `CPRE005` (`mergeable-blocks`, `warning`) is reported at the first block's opening directive, with a `relatedLocations` entry for each later block. The result has a `fix` that deletes the junction and merges the bodies. See [SARIF output](sarif.md).

**Python.** `analyze_source()` returns one `Finding` per run with `kind` `FindingKind.MERGEABLE_BLOCKS`. Its `location` is the first block's opening directive, `directive` and `original_condition` describe that directive, and `related_locations` holds the opening directive of each later block. `edit` is the merge as a `SuggestedEdit` with `FixConfidence.EXACT`; `exact_simplification` and `contextual_simplification` are `None`, and `depends_on_assumptions` is `False`.

```python
import cpre

source = "#if FLAG_A\nint x;\n#endif\n\n#if FLAG_A\nint y;\n#endif\n"
result = cpre.analyze_source(source)

for finding in result.findings:
    if finding.kind is cpre.FindingKind.MERGEABLE_BLOCKS:
        later = [location.line for location in finding.related_locations]
        print(f"line {finding.location.line}: merge with lines {later}")
```

```text
line 1: merge with lines [5]
```

## Why a pair was not reported

| You expected a finding, but | Reason |
| --- | --- |
| there is a comment between the blocks, or on the `#endif` or the next `#if` line | A merge would delete the comment. Remove it, or merge by hand. |
| the first block has an `#else` or `#elif` | Not a plain block; see above. `#if A … #elif B … #endif` followed by `#if B … #endif` is never a simple merge, because the second block must still apply when both `A` and `B` are true. |
| the first body has `#undef A`, `#define A …`, `#include` or `#pragma` | It may change the condition before the second block tests it. |
| one condition is `#if A` and the other `#ifdef A` | A value test and a definedness test are different tests. |
| the condition contains a comparison or a function-like macro | It is an opaque predicate; cpre cannot show the two copies are equal. |
| a block was reported as `DEAD` or `REDUNDANT` | Fix that first; it takes precedence. |
| the file also reports an incomplete conditional | Blocks of an unanalyzed conditional are never reported. Raise the [limits](cli.md#resource-limits). |

## Line numbers

Applying the edit deletes lines, so the lines after it move up. That changes `__LINE__` in the code that follows and the line numbers in later compiler diagnostics. cpre treats the meaning of a rewrite as the preprocessed output apart from those numbers, so this alone does not stop a finding, but a project that depends on exact line numbers should review the diff.

The one case that does stop it is a condition after the edit that depends on `__LINE__`, directly, through a macro of the file, or through a macro of the file that builds names by token pasting (which could form `__LINE__`): moving it could select another branch, so a finding before such a condition is not reported.
