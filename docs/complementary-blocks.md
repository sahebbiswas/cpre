# Complementary blocks

cpre reports two adjacent blocks that test a condition and its opposite, because they are one `#if` with an `#else` written twice:

```c
#if FLAG_A
int x;
#endif

#if !FLAG_A
int y;
#endif
```

```text
L1 ELSE       #if FLAG_A
  else  the opposite condition is tested at line 5; the blocks can be one #if with an #else

Summary  1 complementary block pair
```

The finding carries a suggested edit with `EXACT` confidence. It replaces the first block's `#endif`, the blank lines and the second block's opening directive with an `#else`, keeping the indentation and line endings of the `#endif`:

```c
#if FLAG_A
int x;
#else
int y;
#endif
```

This builds on [Mergeable blocks](mergeable-blocks.md): the same conditions about where the blocks sit, what may be between them and what the first body may contain apply here, and the table at the end of that page explains most reasons a pair is not reported. This page covers what differs.

## What counts as complementary

Two sibling blocks are a pair when every one of these holds:

- **Plain blocks, only blank lines between, no comment on the junction.** Exactly as for merging. A block with an `#else` or `#elif` is never reported, and neither is a pair with a comment or code between the blocks, or a comment on the first block's `#endif` or the second block's opening line.
- **Opposite conditions.** One condition is the Boolean negation of the other: `A` and `!A`, `#ifdef A` and `#ifndef A`, `defined(A)` and `!defined(A)`, `A && B` and `!A || !B`. A name's *value* and its *definedness* are different tests, so `#if A` followed by `#ifndef A` is **not** a pair: with `#define A 0` both are false, and an `#else` would run when neither does.
- **Names and Boolean operators only, reachable, analyzed.** As for merging: no opaque predicate, no `__LINE__` or `__COUNTER__`, and neither block dead or redundant.
- **A body that cannot change the condition.** The first body holds no `#include`, `#pragma`, `_Pragma`, digraph directive, and no `#define` or `#undef` of a name the conditions use or expand to in this file. Without that, the first body could change what the second test would have seen.
- **No regrouping.** A name defined in this file as a function-like macro or as an expression that is not a single token or fully parenthesized (`#define A X || Y`) blocks the pair. The negated form `!A` would expand to `!X || Y`, which is not the opposite of `X || Y`. A definition such as `#define A (X || Y)` is fine.

When three blocks follow each other, only the first pair is reported (`#if A`, `#if !A`, `#if A` reports the first two). Apply the edit and analyze again to see what is left. Two blocks with the *same* condition are reported as [mergeable](mergeable-blocks.md), not as a pair of opposites.

## Output

**Text.** The first block gets an `ELSE` badge (yellow) and an `else` line naming the second block. The `Summary` line counts `complementary block pair`s.

**Exit status.** A pair counts as a finding for `--fail-on-findings`.

**JSON.** In `cpre.analysis/1`, the first block's group has `suggestions: [{"kind": "complementary_blocks", "lines": [5]}]`. `suggestions` is `[]` for every other group. See the [JSON contract](json-contract.md#cpreanalysis1-file-record).

**SARIF.** Rule `CPRE006` (`complementary-blocks`, `warning`) is reported at the first block's opening directive, with a `relatedLocations` entry for the second block and a `fix` that applies the edit. See [SARIF output](sarif.md).

**Python.** `analyze_source()` returns one `Finding` per pair with `kind` `FindingKind.COMPLEMENTARY_BLOCKS`. Its `location` is the first block's opening directive, `related_locations` holds the second block's opening directive, and `edit` is the `#else` rewrite (`FixConfidence.EXACT`). `depends_on_assumptions` is `False`.

Macro assumptions can only remove a finding: a block that assumptions make dead or redundant is reported as `DEAD` or `REDUNDANT` instead, and a pair is reported only if both blocks are also reachable in the analysis without assumptions.

## Macros defined elsewhere

As for merging, macros defined in a header or on the command line are not followed unless you supply them with `-D`/`-U`, `--config-from` or `configuration=`; see [Mergeable blocks](mergeable-blocks.md#macros-defined-elsewhere). Without them, if a header defines `FEATURE` as something that depends on `LEVEL` and the first body does `#undef LEVEL`, cpre cannot see it, so read the pair before accepting the edit when a body defines macros. `--strict-merge` follows nothing; it only declines pairs it cannot vouch for, when a body defines macros and a name is undefined here. It does not change this for replacement text it cannot see: an unseen `#define A X || Y` makes `A` and `!A` both true, so review such a pair even when strict mode is on.

## Line numbers

Applying the edit deletes lines, so the lines after it move up. That changes `__LINE__` in the code that follows and the line numbers in later compiler diagnostics. cpre treats the meaning of a rewrite as the preprocessed output apart from those numbers, so this alone does not stop a finding, but a project that depends on exact line numbers should review the diff. A condition after the edit that depends on `__LINE__` does stop it, as for [mergeable blocks](mergeable-blocks.md#line-numbers). Like that check, it follows only `#define`s in the same file: a condition that reaches `__LINE__` through an included header or a command-line macro does not stop the finding, although the edit may then change the branch it selects.
