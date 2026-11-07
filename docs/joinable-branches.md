# Joinable branches

cpre reports adjacent `#if`/`#elif` branches of one block that have exactly the same body, because they can be one branch that tests both conditions:

```c
#if FLAG_A
return 1;
#elif FLAG_B
return 1;
#elif FLAG_C
return 2;
#endif
```

```text
L1 JOIN       #if FLAG_A
  join  the branches at lines 1 and 3 have the same body; they can be joined into one branch with ||

Summary  1 joinable branch run
```

The finding carries a suggested edit with `EXACT` confidence. It replaces the first joined branch's directive and everything up to the end of the last joined branch's body with one directive and the body once:

```c
#if FLAG_A || FLAG_B
return 1;
#elif FLAG_C
return 2;
#endif
```

An `#elif` is only tested when every earlier test was false, so the joined condition selects the body in exactly the situations where one of the original branches did. Branches after the run are reached under the same conditions as before.

## What counts as joinable

Branches `i` to `j` of a block are a run when all of these hold:

- **Adjacent, with identical bodies.** The bodies are the same text, character for character: whitespace and comments count, but the kind of line ending (LF, CRLF or CR) does not. A comment that differs between two otherwise equal bodies means they are not joined. Nested conditionals inside a body are part of the text.
- **Conditional branches.** Each branch is an `#if`, `#ifdef`, `#ifndef`, `#elif`, `#elifdef` or `#elifndef`. An `#else` is never joined: if an `#else` has the same body as the branch before it, the condition of that branch does not matter at all, which is a different change.
- **Reachable.** Every branch in the run is reachable. A dead or redundant branch is reported as `DEAD` or `REDUNDANT` instead.
- **Plain directive lines.** Each directive is on one physical line, starts with `#` (not a digraph) and carries no comment, because the edit rewrites the first directive and deletes the others.
- **Inert conditions.** No condition depends on `__LINE__` or `__COUNTER__`, directly or through a macro defined in the file; none uses a name that a macro of the file builds with `##` (it could form one of them); and none expands to a `_Pragma`. Joining evaluates a later condition even when an earlier one is true, and on another line, so a pragma or a counter in it would act when it did not before.
- **No later line test.** No condition after the run, in the same chain or anywhere further down, depends on `__LINE__`: the edit moves it up. As elsewhere, only macros defined in the same file are followed, so a condition that reaches `__LINE__` through a header or command-line macro does not stop the finding unless it is supplied with a configuration (see [Mergeable blocks](mergeable-blocks.md#macros-defined-elsewhere)).

Unlike [mergeable](mergeable-blocks.md) and [complementary](complementary-blocks.md) blocks, the bodies of the branches can contain anything, including `#define`, `#undef`, `#include` and `#pragma`, and the conditions may be any expression, including comparisons and function-like macros. Branches of one block are exclusive, so a body can never run before a later branch's test, and the joined test sees the same state as the original tests did.

A run of three or more branches is one finding. Separate runs in the same block are separate findings.

## The joined condition

`#ifdef A` becomes `defined(A)` and `#ifndef A` becomes `!defined(A)`, and the result is always an `#if` (or an `#elif` when the run starts at an `#elif`). A test that is a bare name, a `defined` test or the negation of either is used as it is. Any other test is wrapped in parentheses: `(A > 1) || B`. A bare name is also wrapped when a `#define` in the file gives it a replacement list containing `?` or `,`, since `||` binds tighter than those and the name would regroup.

A macro defined in a header or on the command line is not followed unless you supply it with `-D`/`-U`, `--config-from` or `configuration=` (see [Mergeable blocks](mergeable-blocks.md#macros-defined-elsewhere)); a supplied definition is checked like a `#define` in the file, including for `__COUNTER__`, `__LINE__`, `_Pragma` and regrouping. Without it, if such a macro expands to an expression with a `?:` or comma at the top level, `X || Y` can group differently from the two tests it replaces. Read the edit before applying it when a condition names a macro you cannot see. The same holds for what such a macro does when it is evaluated: a macro from a header that expands to `__COUNTER__` would be evaluated in the joined condition even when the first test is true, so cpre cannot prove that case inert unless that macro is supplied as described above.

A branch after the first of a run is not evaluated when an earlier branch is taken, but in the joined condition it always is. cpre therefore does not join a later branch that calls something other than `defined`, a `__has_*` operator or a function-like macro defined in the same file: `#elif FOO(1)` with no `FOO` is skipped harmlessly today and would fail to preprocess once joined. For the same reason a later branch must be a well-formed expression in shape (operands and operators alternate, parentheses and `?:` pair up): `#elif B C` is never read while an earlier branch is taken, but `A || B C` does not preprocess.

Bodies that differ only in their line endings (LF in one branch, CRLF in another) count as the same body; the kept body is the first branch's.

## Output

**Text.** The first branch of the run gets a `JOIN` badge (yellow) and a `join` line naming the lines of the joined branches. A run that starts at an `#elif` is shown at that `#elif`. The `Summary` line counts `joinable branch run`s.

**Exit status.** A run counts as a finding for `--fail-on-findings`.

**JSON.** In `cpre.analysis/1`, the group has `suggestions: [{"kind": "joinable_branches", "lines": [1, 3]}]`, listing the line of every joined branch including the first. A group can have several entries. See the [JSON contract](json-contract.md#cpreanalysis1-file-record).

**SARIF.** Rule `CPRE007` (`joinable-branches`, `warning`) is reported at the first joined branch's directive, with a `relatedLocations` entry for each later branch and a `fix` that applies the edit. See [SARIF output](sarif.md).

**Python.** `analyze_source()` returns one `Finding` per run with `kind` `FindingKind.JOINABLE_BRANCHES`. Its `location`, `directive` and `original_condition` describe the first joined branch, `related_locations` holds the later branches' directives, and `edit` is the rewrite (`FixConfidence.EXACT`). `depends_on_assumptions` is `False`: macro assumptions can only remove a run, by making a branch dead or redundant; a run is reported only if its branches are also reachable in the analysis without assumptions.

## Line numbers

Applying the edit deletes lines, so the lines after it move up. That changes `__LINE__` in the code that follows (including in the kept body, which now stands where the first branch's body was) and the line numbers in later compiler diagnostics. cpre treats the meaning of a rewrite as the preprocessed output apart from those numbers, so this alone does not stop a finding, but a project that depends on exact line numbers should review the diff. A condition after the edit that depends on `__LINE__` does stop it, as for [mergeable blocks](mergeable-blocks.md#line-numbers); that check follows only macros defined in the same file, so a header or command-line macro that expands to `__LINE__` is not seen and the edit may then change the branch such a later condition selects.
