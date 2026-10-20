# Troubleshooting

How to work out what cpre is telling you, and what to do about it. Messages below are quoted from real runs (`src/driver.c` is the sample from [Getting started](getting-started.md)). New to cpre? Read [Getting started](getting-started.md) first; for ready-made recipes see the [cookbook](cookbook.md).

Contents:

- [Triage in five questions](#triage-in-five-questions)
- [Exit statuses](#exit-statuses)
- [Message index](#message-index)
- [Preprocessing: includes, unknown macros, pragmas](#preprocessing-includes-unknown-macros-pragmas)
- [Analysis limit exceeded](#analysis-limit-exceeded)
- [Findings you did not expect, or did not get](#findings-you-did-not-expect-or-did-not-get)
- [Macro simplification](#macro-simplification)
- [Input and platform issues](#input-and-platform-issues)
- [Python API](#python-api)
- [Debugging toolkit](#debugging-toolkit)
- [Reporting a problem](#reporting-a-problem)

## Triage in five questions

1. **What was the exit status?** `0` completed, `1` completed with findings (only with `--fail-on-findings`, or `cover` with uncovered outcomes, or `simplify-macros --check` with a pending rewrite), `2` could not complete. Status `2` always comes with a message on stderr; read it before anything else.
2. **Which command was it?** `cpre FILE` and `simplify-macros` reason symbolically and never read other files. `preprocess` and `cover` pick concrete configurations and need includes and macro values. Most surprises come from running a concrete command with symbolic expectations.
3. **Is the result complete?** Look for "analysis limit exceeded", "not analyzed", "preprocessing was incomplete" or `"complete": false`. An incomplete result says nothing about the parts it did not reach; an empty report is not a clean one.
4. **Did cpre know what it needed?** Unknown macros, include directories and `__has_include` answers are never guessed. cpre fails with a hint instead.
5. **Can you shrink it?** `cpre test-input 'EXPR'` evaluates a single condition, and piping three lines through `cpre -` reproduces most issues (see [Reporting a problem](#reporting-a-problem)).

## Exit statuses

| Command | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `cpre FILE…` | Complete (findings allowed) | Complete, `--fail-on-findings`, finding present | Read/parse error, incomplete analysis, bad options |
| `cpre simplify-macros` | Complete | `--check` with a pending rewrite, or `--fail-on-findings` with a finding. `--diff` alone exits `0` | Read error, a macro could not be fully analyzed, rewrite verification failed, bad options |
| `cpre preprocess` | Complete (source written) | not used | Incomplete or error (no partial source is written) |
| `cpre cover` | Every satisfiable outcome covered | Some outcome not covered | Error or incomplete |
| `cpre test-input` | Evaluated | not used | Malformed expression or resource limit |

Status `2` outranks `1`. With several inputs, one failure makes the whole run `2` even when other files have findings. Full contract: [Exit status contract](cli.md#exit-status-contract).

## Message index

| You see | Meaning | Go to |
| --- | --- | --- |
| `include processing is not supported` | A reachable `#include` and no way to resolve it | [Includes](#includes) |
| `cannot resolve #include <x.h>` | Search directories were given but the header was not found | [Includes](#includes) |
| `condition is not determined by the current macro state` | A reachable condition needs a macro you did not set | [Unknown macros](#unknown-macros) |
| `__has_include requires caller-provided include availability` | `__has_include` with no answer | [`__has_include`](has-include.md) |
| `#pragma preprocessing directive is not supported …` | Reachable pragma that you have not accepted | [Pragmas and `#error`](#pragmas-and-error) |
| `#error preprocessing directive is not supported …` | A reachable `#error` | [Pragmas and `#error`](#pragmas-and-error) |
| `analysis limit exceeded for atoms\|bdd_nodes\|work` | Bounded reasoning ran out | [Analysis limit exceeded](#analysis-limit-exceeded) |
| `conditional at lines 6-8 not analyzed … findings cover only the rest of the file` | Partial result | [Analysis limit exceeded](#analysis-limit-exceeded) |
| `'utf-8' codec can't decode byte …` | File is not UTF-8 | [Input](#input-and-platform-issues) |
| `expected a Boolean expression at line N, column M` | Malformed `#if` | [Input](#input-and-platform-issues) |
| `#if has no matching #endif at line N`, `duplicate #else`, … | Malformed conditional structure | [Input](#input-and-platform-issues) |
| `division by zero in concrete condition` | A reachable condition divides by zero | [Input](#input-and-platform-issues) |
| `proven equivalent; not rewritten: parenthesize the original first` | Simplifiable, but rewriting could change meaning | [Macro simplification](#macro-simplification) |
| `would rewrite PATH` (stderr, `--check`) | A verified rewrite is pending | [Macro simplification](#macro-simplification) |
| `error: unsupported symbolic literal 5; supported literals: 0, 1` | Only `0` and `1` can be symbolic | [Macro simplification](#macro-simplification) |

## Preprocessing: includes, unknown macros, pragmas

### Includes

cpre never searches the filesystem on its own. A reachable `#include` stops preprocessing until you decide how to treat it:

```text
$ cpre preprocess src/driver.c -D DEBUG
src/driver.c: line 1: include processing is not supported
src/driver.c: hint: pass -I DIR (or --iquote DIR) to resolve includes, or --skip-includes to mask them
```

| Goal | Option |
| --- | --- |
| Follow headers from your tree | `-I include` (for `<…>` and `"…"`), `--iquote src` (for `"…"` only) |
| Ignore headers you do not have | `--skip-includes` (their macros are **not** assumed) |
| Follow what resolves, skip system headers | `-I include --skip-includes` |
| Bound recursion | `--max-include-depth N` |

After you add `-I`, a header that is not found fails by name rather than silently disappearing:

```text
$ cpre preprocess sys.c -I include
sys.c: line 1: cannot resolve #include <stdio.h>
```

Add `--skip-includes` to skip just the unresolved ones. Includes inside discarded branches are never touched. If skipping hides macros your conditions need, supply them by hand with `-D`, or seed them from a header with `--config-from`. Skipped includes are reported on stderr (`skipped #include <stdio.h>`) and in JSON as `skipped_includes`. `cpre` and `cpre simplify-macros` do not need any of this: they never read included files. See [Include resolution](include-resolution.md).

### Unknown macros

```text
src/driver.c: line 13: condition is not determined by the current macro state: #ifdef DEBUG (unresolved: DEBUG (definedness))
src/driver.c: hint: supply -D DEBUG or -U DEBUG
```

cpre will not decide a condition that depends on a macro it was not told about. The hint says how: `-D NAME[=VALUE]` to define it, `-U NAME` to mark it undefined. For value uses (`#if LEVEL >= 2`) the hint is `-D LEVEL=<value>`, or `-U LEVEL` to evaluate it as `0`. To handle many at once:

| Situation | Do this |
| --- | --- |
| See everything a file needs | `cpre preprocess FILE --list-unknown-macros` |
| The values live in a project header | `--config-from include/config.h` (explicit `-D`/`-U` still win) |
| You want compiler-like "not defined means 0" | `--unknown-names undefined` |
| See the macro state cpre starts from | `--dump-macros` |
| Cover instead of choosing | `cpre cover FILE` |

`--config-from` fails before reading the target if the seed has an active `#include`, a condition it cannot decide, or redefines a `--standard-macro`; the message begins `PATH: invalid --config-from seed:`. A seed `#ifndef LEVEL` is undecided in open-world mode, so pair it with `--config-from-unknown-names undefined` to read the seed with normal C semantics while the target stays open. Predefined macros such as `__GNUC__`, `__STDC_VERSION__` and `__x86_64__` are unknown unless you supply them (`-D __GNUC__=13`) or give `--standard-macro NAME=VALUE` for the standard ones; cpre does not inspect your compiler. See [Concrete macro configuration](concrete-configuration.md) and [Concrete preprocessing](preprocessing.md#deterministic-predefined-macro-context).

#### `__has_include`

```text
hi.c: line 1: __has_include requires caller-provided include availability
```

Answer it explicitly with `--has-include '<zlib.h>=1'` (quote it; `=0` means unavailable), or let the `-I`/`--iquote` search decide with `--has-include-from-search`. An explicit answer wins over the search. See [Host-assisted `__has_include`](has-include.md).

### Pragmas and `#error`

Reachable `#pragma` and `#error` directives make concrete preprocessing incomplete rather than being ignored, because cpre cannot know what a pragma means for your build:

```text
$ cpre preprocess e.h -D A
e.h: line 1: #pragma preprocessing directive is not supported during concrete preprocessing
e.h: hint: pass --consume-pragma PREFIX to accept pragmas you know are harmless (for example --consume-pragma once)
```

This includes `#pragma once` in the file you pass directly (inside an included header it is accepted). You decide which pragmas are harmless for your run with `--consume-pragma PREFIX` (repeatable), on `cpre preprocess` and `cpre cover`:

```bash
cpre preprocess e.h -D A --consume-pragma once --consume-pragma pack
```

A prefix matches the first tokens of the pragma's payload, so `once` accepts `#pragma once`, `pack` accepts `pack(push, 1)` and `pack(pop)` but not `packed`, and `GCC diagnostic` accepts every `GCC diagnostic …` pragma. It also matches `_Pragma("…")` operators. Accepted pragmas are removed from the output; anything else still makes the result incomplete, and the message then names it so you can add a prefix:

```text
e.h: line 2: reachable pragma is unsupported by the caller's pragma handler: pack(push, 1)
e.h: hint: add --consume-pragma pack to accept this pragma
```

A header that has `#pragma once` usually also has an include guard, which needs `-U GUARD_NAME` next; the [header recipe](cookbook.md#preprocess-a-header-pragma-once-and-include-guards) walks through both. `cpre FILE` and `cpre simplify-macros` are not affected, and pragmas in branches that are not selected are never examined. From Python, pass `pragma_handler=` ([Pragma handling](pragma-handling.md#command-line)).

A reachable `#error` is a real result: it means that configuration is not buildable. Pick a configuration that does not reach it (`-D`/`-U`), or analyze symbolically with `cpre FILE`. `#error` in a branch that is not selected is never reported.

## Analysis limit exceeded

```text
big.c: line 1: conditional at lines 1-2 not analyzed (analysis limit exceeded for atoms: 70 > 64); findings cover only the rest of the file
```

cpre's Boolean reasoning is bounded so it always terminates deterministically. Three limits exist:

| Limit | Default | Applies to | Typical cause |
| --- | --- | --- | --- |
| `--max-atoms` | 64 | each independent component | One huge `#if A && B && …`, or many groups chained through shared macros |
| `--max-bdd-nodes` | 100000 | each independent component | Conditions that XOR many variables (checksums, bit-field style tests) |
| `--max-work` | 500000 | the whole analysis | Very large generated tables of conditions |

The message names which one and gives `observed > limit`. A "component" is a set of conditional groups connected by shared macros, so many unrelated `#ifdef FEATURE_n` blocks never add up (500 of them complete under the default). When only some components exceed a limit the result is **partial**: the findings you get are proven, the named conditionals were not analyzed, and the exit status is `2`. When the shared `max_work` budget runs out, or a limit is hit while checking the supplied assumptions, nothing is reported.

What to do, in order:

1. **Look at the cost** with `--telemetry` (stderr, or a `telemetry` object with `--json`):

   ```text
   big.c: telemetry: atoms=71 components=2 work=27/500000 (limits: atoms<=64 bdd_nodes<=100000)
   big.c: telemetry: component 0: atoms=70 groups=1 nodes=0 incomplete (atoms)
   big.c: telemetry: component 1: atoms=1 groups=1 nodes=2 analyzed
   ```

   It shows which component is large and which limit it hit. Telemetry is diagnostic only; it does not change findings or exit status.
2. **Raise the limit** that was hit, for that run only: `cpre --max-atoms 128 big.c`. The same options exist on `simplify-macros` and `cover`; in Python use `cpre.AnalysisOptions(max_atoms=…, max_bdd_nodes=…, max_work=…)`. Larger limits cost time and memory, and work grows quickly with atoms, so raise by modest steps.
3. **Exclude** machine-generated or vendored files from the CI command if their conditions are not meant to be reviewed.
4. **Do not** treat the file as clean. The partial report is accurate for what it covers, but the unanalyzed conditionals have no findings because they were not examined.

`simplify-macros` reports the same limits per macro (`analysis limit exceeded for atoms: …`); an affected macro is never rewritten, the other macros in the file still are, and the run exits `2`. For `preprocess` and `cover`, `max_atoms` and `max_bdd_nodes` apply per reachable condition; see [Resource limits](preprocessing.md#resource-limits).

## Findings you did not expect, or did not get

**"Why is nothing reported?"** The default report lists only notable branches. Use `--verbose` for every branch and macro. Then check completeness: stderr message, exit status `2`, or `"complete": false` in JSON mean parts were not analyzed.

**"Why is `#if LEVEL >= 2` not dead or redundant?"** Comparisons and arithmetic that cpre cannot decide symbolically are *opaque predicates*: they are carried through the logic as unknown facts. The repeated `#elif LEVEL >= 2` after `#if LEVEL >= 2` is reported dead, but `LEVEL >= 3` after `LEVEL >= 2` is not, because cpre does not claim integer reasoning in the symbolic report. (For concrete values, `preprocess` evaluates the full integer expression grammar. `cover` solves simple integer comparisons.)

**"Why is this branch `[reachable]` when I know `DEBUG` is off in production?"** Symbolic analysis is configuration-independent: an unmentioned macro could be either. If you know the build state, say so in Python with `assumptions={"DEBUG": False}` and read `Finding.depends_on_assumptions`; or choose the configuration with `cpre preprocess -U DEBUG`. cpre never silently assumes an unknown macro is false.

**"Why is a branch reported dead when I defined the macro on the command line?"** The default `cpre FILE` ignores your build flags and `#include`d headers. A branch is dead only if no configuration at all selects it. If you want the answer for your configuration, use `preprocess`.

**"Why does `defined(X)` differ from `X`?"** They are different atoms: `defined(X)` is definedness, `X` is the value (true when defined as non-zero). cpre relates them with the C rule that a true value implies defined.

**Dead and redundant are not instructions to delete.** cpre never suggests removing a dead branch mechanically; apply only edits it provides as a structured `SuggestedEdit`.

## Macro simplification

| Symptom | Cause and fix |
| --- | --- |
| `proven equivalent; not rewritten: parenthesize the original first` | The replacement list is a bare `a && b` / `a \|\| b`. Parenthesizing it would change how its uses parse, so cpre reports without rewriting. Wrap the definition in parentheses yourself and rerun; it becomes rewritable. |
| `--check` exits `1` and prints `would rewrite PATH` | Expected: a verified rewrite is pending. `--diff` shows it; `--rewrite` applies it. |
| `--diff` exits `0` though it prints a diff | By design; add `--check` to gate on it. |
| A macro using `0` as a disabled switch, or `1` as an always-on one, simplifies away a term you wanted | Use `--symbolic-literal 0` (or `1`, or both by repeating the option) so the literal stays a symbolic atom. Only `0` and `1` can be symbolic. |
| `error: --rewrite cannot be combined with --diff or --check` | These modes are mutually exclusive; pick one. |
| `standard input cannot be rewritten in place` | Use `--diff` or `--check` with `-`, or name a real file. |
| Exit `2` after `--rewrite` with a verification message | cpre re-parses and re-analyzes every rewrite and aborts, writing nothing, if the result is not equivalent. Please report it ([below](#reporting-a-problem)). |
| A function-like or string macro is not listed | Not a candidate; `--verbose` lists skipped macros with the reason. |

How verification works and what each outcome means is in [Semantic verification](macro-simplification.md#semantic-verification).

## Input and platform issues

| Problem | Details |
| --- | --- |
| Non-UTF-8 source | cpre reads UTF-8 only: `bad.c: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte`, exit `2`. Convert first (`iconv -f latin1 -t utf-8 old.c > new.c`) or pipe the converted text through `-`. |
| Path not found, directory without `--recursive` | Usage errors, exit `2` (`nope.c: no such file or directory`, `src: is a directory; use --recursive to scan it`). Recursive scans match `.c .cc .cpp .cxx .h .hh .hpp .hxx`; name other files explicitly. |
| `expected a Boolean expression at line 1, column 1` | A malformed `#if`/`#elif` (here an empty one). Location points at the problem. cpre fails on malformed conditionals instead of skipping them, so a typo in a branch that never builds is still reported. |
| `#if has no matching #endif`, `duplicate #else`, unmatched `#endif`/`#else`/`#elif` | Structure errors with the directive's line and column. Use [`parse_conditionals()`](conditional-structure.md) when you need lossless recovery diagnostics instead of an error. |
| `division by zero in concrete condition` | A reachable `#if 1/0`. Pick a configuration that does not reach it. |
| CRLF, tabs, form feeds, backslash continuations | Handled like GCC and Clang: `\n`, `\r\n` and lone `\r` end lines; `simplify-macros --rewrite` preserves the file's line endings exactly. |
| Windows paths in output | JSON escapes backslashes as JSON does, and `--dump-macros` doubles them; parse `--json` rather than scraping text. |
| Redirecting output | Reports go to stdout, diagnostics to stderr. `cpre … > report.txt` leaves problems on the console. |

## Python API

| Symptom | Cause and fix |
| --- | --- |
| `result.findings` is empty but the file has issues | Check `result.complete` first. `result.partial` means findings are proven but parts were not analyzed (`result.incomplete_components`); neither is a clean result. |
| `CpreError` raised | Malformed input or an invalid argument. Use `error.code` ([`ErrorCode`](api.md#structured-errors)), `error.location` and `error.message`, not `str(error)`. |
| `INVALID_ASSUMPTIONS` / `INVALID_CONFIGURATION` | A bad macro name or contradictory definition passed in. The message names the offending entry. |
| `preprocess_source` returns `complete=False` and `source is None` | Atomic incomplete: read `result.incomplete` (each has `code`, `message`, `location`, and for unresolved conditions `unresolved`). No partial source exists by design. |
| `AttributeError` or `ImportError` on a `cpre.something` name | Only names in `cpre.__all__` are supported ([API index](api.md#public-api-index)); internal modules may change without notice. |
| Results differ across versions | Ordering and field sets follow the [JSON contract](json-contract.md) and [downstream compatibility](downstream-compatibility.md) policies; telemetry and text output are explicitly not contracts. `cpre.__version__` identifies the release. |

## Debugging toolkit

| I want to… | Use |
| --- | --- |
| Confirm the install | `cpre --version` or `python -m cpre --version` |
| See every branch and macro | `cpre --verbose FILE` |
| Get structured data to inspect | `cpre --json FILE`, [json-contract.md](json-contract.md) |
| See where analysis cost went | `cpre --telemetry FILE` |
| Try one condition in isolation | `cpre test-input 'A && (A \|\| B)'` |
| See what macro state cpre starts from | `cpre preprocess FILE --dump-macros …` |
| List what a file depends on | `cpre preprocess FILE --list-unknown-macros` |
| See which includes were skipped | stderr of `preprocess --skip-includes`, or `skipped_includes` in `--json` |
| Reproduce from a pipe | `printf '#if A\n#endif\n' \| cpre -` |

## Reporting a problem

Open an issue at <https://github.com/sahebbiswas/cpre/issues> with:

- `cpre --version` and your Python version and OS;
- the exact command line and exit status;
- the smallest source that reproduces it (three lines through `cpre -` is ideal) and, for preprocessing, the `-D`/`-U`/`-I` options;
- stderr, and `--json` output if the problem is in a report;
- what you expected, and which contract you expected it from (a doc link is perfect).

For "cpre said clean but a compiler disagrees", include the compiler, its version, and the `-E` output; see [Compiler differential tests](compiler-differential.md) for how those comparisons are made.
