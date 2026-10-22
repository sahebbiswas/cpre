# Getting started

This page takes you from installation to a first useful result in about ten minutes. Each step uses a small sample project and shows the real output, so you can check that your install behaves the same. When something goes wrong, the [troubleshooting guide](troubleshooting.md) maps every message to its fix; the [cookbook](cookbook.md) has ready-made recipes for CI, cleanup and downstream tooling.

## What cpre does for you

cpre reads C/C++ preprocessor conditionals (`#if`, `#ifdef`, `#elif`, …) and macro definitions as Boolean logic, without compiling or parsing the rest of the file. It answers four kinds of question:

| You want to know… | Use | Result |
| --- | --- | --- |
| Which branches are dead or redundant, and which conditions can be simplified? | `cpre FILE` | A report (text, JSON or SARIF). Never changes your source. |
| Which `#define` replacement lists can be written more simply, and can cpre do it safely? | `cpre simplify-macros FILE` | A report, a diff, a CI check, or an in-place rewrite. |
| What does the file look like for one concrete set of `-D`/`-U` choices? | `cpre preprocess FILE -D …` | The selected, macro-expanded source. |
| Which configurations do I need to exercise every branch? | `cpre cover FILE` | A short list of `-D`/`-U` argument lists. |

cpre is a reasoning tool, not a compiler front end. It does not know your compiler's predefined macros, your include paths or your build flags until you tell it, and it says so explicitly (rather than guessing) whenever the answer depends on something it was not given.

## Install

cpre needs Python 3.9 or later and has no runtime dependencies.

```bash
python -m pip install cpre
cpre --version
```

`cpre --version` prints `cpre X.Y.Z`. If the `cpre` command is not found, the scripts directory of your Python environment is not on `PATH`; `python -m cpre --version` is equivalent and always works. Installing into a virtual environment (`python -m venv .venv`) keeps the command and the Python API together.

To work from a checkout instead, see [Development and testing](https://github.com/sahebbiswas/cpre/blob/main/README.md#development-and-testing).

## The sample project

Create these two files. They are small on purpose and contain one example of each thing cpre reports.

`include/config.h`:

```c
#ifndef CONFIG_H
#define CONFIG_H
#define TURBO
#define LEVEL 2
#endif
```

`src/driver.c`:

```c
#include "config.h"

#define FEATURE_FAST (0 && LEGACY) || TURBO

#if defined(TURBO) || defined(TURBO)
int turbo(void);
#elif defined(TURBO) && defined(SLOW)
int never(void);
#else
int normal(void);
#endif

#ifdef DEBUG
#  ifdef DEBUG
int log_it(void);
#  endif
#endif

#if LEVEL >= 2
int level2(void);
#endif
```

## Step 1: Analyze conditionals

```bash
cpre src/driver.c
```

```text
Conditionals
L5  SIMPLIFY   #if defined(TURBO) || defined(TURBO)
  - TURBO || TURBO
  + TURBO
L7  DEAD       #elif defined(TURBO) && defined(SLOW)
  why  condition contradicts its parent or earlier branches
  - TURBO && SLOW
  + 0  (always false in context)
L13 ok         #ifdef DEBUG
│ L14 REDUNDANT  #ifdef DEBUG
│   why  condition is always true in this branch context
│   - DEBUG
│   + 1  (always true in context)

Macros
L3 SIMPLIFY   #define FEATURE_FAST
   - (0 && LEGACY) || TURBO
   + (!!TURBO)  (proven equivalent)
   hint  not rewritten: parenthesize the original first

Summary  1 dead · 1 redundant · 1 simplifiable condition · 1 simplifiable macro
```

How to read it:

- Every entry shows the line, a badge, and the directive. A `-` line is the condition (as cpre normalizes it, so `defined(TURBO)` is written `TURBO`) and the `+` line under it is what it reduces to.
- **`SIMPLIFY`** means the branch can be taken and its condition has a shorter equivalent: `TURBO || TURBO` is just `TURBO`.
- **`DEAD`** means the branch can never be selected. The `#elif` on line 7 is dead because an earlier branch already covers every case where `TURBO` is defined, so its condition is `+ 0`.
- **`REDUNDANT`** means the condition is always true where it sits (`+ 1`). The inner `#ifdef DEBUG` is already guaranteed by the outer one. The `ok` line and the `│` guide show that it is nested inside line 13, which is listed only to give that context.
- The `Macros` entry is a macro finding: the replacement list can be simplified, but cpre will not rewrite it because the added parentheses could change what its uses mean (the `hint` line says so).
- The `Summary` line totals the findings.
- `#if LEVEL >= 2` is not reported. cpre treats comparisons it cannot decide symbolically as opaque predicates; `--verbose` shows them.

On a terminal the report is colored: red for what is dead or removed, yellow for redundant, and green only for the result of a transformation. The layout is the same without color. See [Default text reporting](cli.md#default-text-reporting) for the full legend, `--color`, and the progress line that appears on large recursive runs.

By default only notable branches are listed. `--verbose` lists every branch and macro. The exit status is `0` here even though there are findings: cpre only fails the process on findings when you ask it to.

```bash
cpre --fail-on-findings src/driver.c; echo $?     # 1: dead or redundant branch found
```

| Exit status | Meaning |
| --- | --- |
| `0` | Completed. Findings may exist (unless `--fail-on-findings`). |
| `1` | `--fail-on-findings` and a dead, redundant or simplifiable item was found. |
| `2` | A file could not be read, analysis could not finish, or the command line was invalid. |

Status `2` is never "clean". If cpre could not finish, an empty report does not mean the file is fine; it prints why on stderr. See [Exit status contract](cli.md#exit-status-contract).

## Step 2: Tidy macro definitions

```bash
cpre simplify-macros src/driver.c         # report only
cpre simplify-macros --diff FILE          # show the rewrite as a unified diff
cpre simplify-macros --check FILE         # exit 1 if a rewrite is available (CI gate)
cpre simplify-macros --rewrite FILE       # apply it
```

Only the first three are read-only. `--rewrite` is the only mode that writes, and it applies only edits cpre has proven equivalent and re-verified after rewriting. `FEATURE_FAST` above is reported but not rewritten, so `--check` exits `0` for the sample. If you wrap the replacement list in parentheses, `#define FEATURE_FAST ((0 && LEGACY) || TURBO)`, it becomes rewritable. See [Macro Boolean simplification](macro-simplification.md#semantic-verification) for what "proven" covers.

## Step 3: Preprocess one configuration

Run the preprocessor on the sample as it stands:

```bash
cpre preprocess src/driver.c -D DEBUG
```

```text
src/driver.c: line 1: include processing is not supported
src/driver.c: hint: pass -I DIR (or --iquote DIR) to resolve includes, or --skip-includes to mask them
```

This is the most common first stumble, and it is deliberate: cpre never searches the filesystem unless you give it search directories. Choose one:

```bash
cpre preprocess src/driver.c -I include -D DEBUG --compact       # read config.h from include/
cpre preprocess src/driver.c --skip-includes -D TURBO -D LEVEL=2 -D DEBUG --compact   # ignore includes, supply their macros by hand
```

```text


int turbo(void);

int log_it(void);

int level2(void);
```

Canonical output keeps every source line in place (so line numbers match the original) by blanking the lines it removed; `--compact` collapses those blanks for reading. Use canonical output when a downstream tool needs line numbers or source maps, and `--compact` for humans.

Without `-D DEBUG` cpre refuses to guess:

```text
src/driver.c: line 13: condition is not determined by the current macro state: #ifdef DEBUG (unresolved: DEBUG (definedness))
src/driver.c: hint: supply -D DEBUG or -U DEBUG
```

To list everything a file's conditions need before you start:

```bash
cpre preprocess src/driver.c --list-unknown-macros
```

```text
DEBUG	definedness	13,14
LEVEL	value	19
SLOW	definedness	7
TURBO	definedness	5,7
```

The listing is a conservative over-approximation that does not read included headers, which is why `TURBO` and `LEVEL` appear even though `config.h` defines them. Pointing cpre at that header with `--config-from include/config.h` (or a `-I` directory) supplies them. If you prefer the compiler's closed-world view, `--unknown-names undefined` treats every name you did not set as undefined. See [Concrete preprocessing](preprocessing.md) and [Concrete macro configuration](concrete-configuration.md).

Running on a header directly? Headers usually carry `#pragma once` and an include guard; both need a decision from you. See [Preprocess a header](cookbook.md#preprocess-a-header-pragma-once-and-include-guards): in short, `--consume-pragma once` and `-U GUARD_NAME`.

## Step 4: Find configurations that cover every branch

```bash
cpre cover src/driver.c --skip-includes
```

Each line is an argument list for `cpre preprocess`; together they select every reachable branch outcome. Lines starting with `#` explain outcomes that cannot be covered. Exit status `1` means at least one outcome is uncovered. See [Branch-covering configurations](branch-coverage.md).

## Step 5: Use it from Python

Everything above is available as functions on the top-level `cpre` module:

```python
import cpre

source = """\
#ifdef DEBUG
#  ifdef DEBUG
int log_it(void);
#  endif
#endif
"""

result = cpre.analyze_source(source, filename="driver.c")
if not result.complete:
    # Never treat an empty findings tuple as "clean" unless complete is True.
    for diagnostic in result.incomplete:
        print("analysis incomplete:", diagnostic.code, diagnostic.resource)
for finding in result.findings:
    print(finding.kind.value, finding.location.line, finding.reason)
```

```python
import cpre

source = "#ifdef DEBUG\nint log_it(void);\n#endif\nint always;\n"

preprocessed = cpre.preprocess_source(source, filename="driver.c", assumptions={"DEBUG": True})
if preprocessed.complete:
    print(cpre.compact(preprocessed))
else:
    for diagnostic in preprocessed.incomplete:
        print(diagnostic.location.line, diagnostic.message)
```

The two rules that matter most when embedding cpre:

1. **Check `complete` before trusting an empty result.** Incomplete results are returned, not raised.
2. **Catch `cpre.CpreError`** for malformed input, and branch on `error.code` rather than parsing messages.

The [Python API guide](api.md) has the full model, including the [public API index](api.md#public-api-index) and [typical setups](api.md#typical-setups).

## Where to go next

| I want to… | Read |
| --- | --- |
| Gate pull requests, upload SARIF, add a pre-commit hook | [Cookbook](cookbook.md) |
| Understand a message or non-zero exit status | [Troubleshooting](troubleshooting.md) |
| Look up every flag and the JSON/exit contracts | [CLI guide](cli.md), [JSON contract](json-contract.md) |
| Embed cpre in an analyzer | [Python API guide](api.md), [Downstream compatibility](downstream-compatibility.md) |
| Understand the soundness guarantees | [Exact Boolean queries](boolean-queries.md), [Macro simplification](macro-simplification.md) |
