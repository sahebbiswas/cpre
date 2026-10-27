# Cookbook

Task-oriented recipes for the things people most often do with cpre. Each recipe names the command, what it guarantees, and the exit status to rely on. The examples use the sample project from [Getting started](getting-started.md#the-sample-project) (`src/driver.c` and `include/config.h`); the Python blocks in this guide run unchanged against it. For a message you do not recognise, see [Troubleshooting](troubleshooting.md).

Contents:

- [Fail CI on dead or redundant branches](#fail-ci-on-dead-or-redundant-branches)
- [Publish findings as code-scanning alerts (SARIF)](#publish-findings-as-code-scanning-alerts-sarif)
- [Check only the files a pull request changed](#check-only-the-files-a-pull-request-changed)
- [Run cpre as a pre-commit hook](#run-cpre-as-a-pre-commit-hook)
- [Clean up macro definitions safely](#clean-up-macro-definitions-safely)
- [Find which macros a file depends on](#find-which-macros-a-file-depends-on)
- [Feed one configuration to a downstream parser](#feed-one-configuration-to-a-downstream-parser)
- [Preprocess a header (`#pragma once` and include guards)](#preprocess-a-header-pragma-once-and-include-guards)
- [Build a configuration matrix for tests](#build-a-configuration-matrix-for-tests)
- [Analyze a file from git history or a pipe](#analyze-a-file-from-git-history-or-a-pipe)
- [Experiment with a Boolean expression](#experiment-with-a-boolean-expression)
- [Embed cpre in your own analyzer](#embed-cpre-in-your-own-analyzer)

## Fail CI on dead or redundant branches

```bash
cpre --recursive --fail-on-findings src include
```

| Status | CI meaning |
| --- | --- |
| `0` | Clean: analysis finished and nothing needs attention. |
| `1` | Findings: a dead, redundant or simplifiable item exists. |
| `2` | Could not decide: unreadable file, malformed directive, or an analysis limit was hit. Treat as a failure. |

Status `2` outranks `1` and is never a pass. A common first-week surprise is a generated or vendored file that hits the analysis limit; exclude it from the command, or raise the limit for that run with `--max-atoms` (see [Analysis limits](troubleshooting.md#analysis-limit-exceeded)).

`--recursive` scans `.c .cc .cpp .cxx .h .hh .hpp .hxx` under each directory. To scan other suffixes or only some files, name the files explicitly (see the next recipe); a named file is analyzed whatever its suffix.

To check macro definitions as well as conditionals, nothing extra is needed: `cpre` analyzes both. Use `--no-macros` or `--no-conditionals` to narrow it. To enforce only that macros are already simplified:

```bash
cpre simplify-macros --check --recursive src      # exit 1 if a verified rewrite is available
```

## Publish findings as code-scanning alerts (SARIF)

```yaml
# .github/workflows/cpre.yml
name: cpre
on: [pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install cpre
      - name: Analyze
        run: cpre --recursive --sarif --fail-on-findings src include > cpre.sarif
      - name: Upload
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: cpre.sarif
```

The analysis step writes the SARIF file before it sets the exit status, and `if: always()` uploads it even when the step fails, so findings show up as alerts and the job still gates the merge. Drop `--fail-on-findings` to publish alerts without failing the build. Incomplete analysis and unreadable files are carried in the SARIF as tool notifications and still exit `2`. Pin a cpre version in CI (`cpre==X.Y.Z`) so a release cannot change results between runs. Rule ids and fixes are described in [SARIF output](sarif.md).

## Check only the files a pull request changed

```bash
set -o pipefail   # a failed git diff must fail the check, not look like "no files"
git diff --name-only -z --diff-filter=d origin/main...HEAD \
    -- '*.c' '*.cc' '*.cpp' '*.cxx' '*.h' '*.hh' '*.hpp' '*.hxx' \
  | xargs -0 -r cpre --fail-on-findings
```

`-z` and `xargs -0` keep paths with spaces intact, `--diff-filter=d` leaves out deleted files, and `-r` (GNU `xargs`) skips cpre entirely when nothing matched. cpre has no whole-project state, so analyzing a subset gives the same answer for those files as a full scan.

## Run cpre as a pre-commit hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: cpre
        name: cpre
        entry: cpre --fail-on-findings
        language: system
        files: '\.(c|cc|cpp|cxx|h|hh|hpp|hxx)$'
```

`language: system` uses the `cpre` on `PATH`; install it in the same environment as `pre-commit`. To rewrite macros automatically instead, use `entry: cpre simplify-macros --rewrite`: pre-commit then fails the commit when the hook modified files, and you stage the result.

## Clean up macro definitions safely

Work from the least to the most invasive mode:

```bash
cpre simplify-macros src/driver.c            # what could be simplified, and why
cpre simplify-macros --diff src/driver.c     # exactly what would change
cpre simplify-macros --rewrite src/driver.c  # apply it
git diff                                      # review like any other change
```

`--rewrite` applies only edits cpre proved equivalent and then re-parses and re-analyzes the result; if that verification fails nothing is written and the exit status is `2`. Macros it reports but will not rewrite are listed with a reason, for example an unparenthesized `a && b` replacement, whose uses could change meaning once parenthesized. Simplified lone identifiers keep the `!!X` normalization so the macro's numeric value is preserved. Line endings, indentation, comments and unrelated code are left byte-for-byte alone. If your project uses `0` as a deliberately disabled switch (`(0 && FEATURE) || OTHER`), add `--symbolic-literal 0` so the disabled term is preserved. See [Macro Boolean simplification](macro-simplification.md).

## Find which macros a file depends on

```bash
cpre preprocess src/driver.c --list-unknown-macros
```

```text
DEBUG	definedness	13,14
LEVEL	value	19
SLOW	definedness	7
TURBO	definedness	5,7
```

Columns are the macro, whether conditions test its definedness or evaluate its value, and the directive lines that depend on it. It is a conservative over-approximation (it does not read included headers), suitable as a checklist for a build configuration. The same data as Python:

```python
import cpre

source = open("src/driver.c", encoding="utf-8").read()
for item in cpre.unknown_macros_in_source(source, filename="src/driver.c"):
    uses = [use.value for use in item.uses]
    print(item.name, uses, [location.line for location in item.locations], item.suggestions)
```

Once you know the names, seed them from a project header rather than retyping them: `--config-from include/config.h` reads that file's final macro state as the starting configuration, and `-D`/`-U` always win over it. Add `--unknown-names undefined` for compiler-like closed-world behavior. Details: [Concrete macro configuration](concrete-configuration.md#construction-from-source).

## Feed one configuration to a downstream parser

```bash
cpre preprocess src/driver.c -I include --config-from include/config.h -D DEBUG --json > driver.json
```

`--json` carries the preprocessed `source`, the `complete` flag, `diagnostics` and `skipped_includes` (schema `cpre.preprocess/1`); without it, stdout is the transformed source alone. The command line does not emit the source map; if you need to map output positions back to the original and to headers, use the Python API below. Rules to build on:

- Output keeps one line per input line (removed lines are blanked), so line numbers survive. `--compact` removes the blanks but is for humans; the Python `source_map` describes the canonical form only.
- An incomplete result never produces partial source. `complete: false` plus exit status `2` means "do not parse this".
- Includes are resolved only from the directories you pass with `-I`/`--iquote`. Use `--skip-includes` to ignore headers you do not have (their macros are then not assumed), and `-I` together with `--skip-includes` to follow the ones that resolve and skip the rest.
- Reachable pragmas fail closed. Accept the harmless ones with `--consume-pragma once --consume-pragma pack`; they are removed from the output, and any other pragma still makes the result incomplete.
- `__has_include` needs an answer: `--has-include '<zlib.h>=1'`, or `--has-include-from-search` to let the `-I` search decide.

The same thing from Python, with includes resolved from a directory and `DEBUG` assumed defined:

```python
import cpre
from cpre.includes import SearchPathResolver

source = open("src/driver.c", encoding="utf-8").read()
result = cpre.preprocess_source(
    source,
    filename="src/driver.c",
    include_resolver=SearchPathResolver(["include"]),
    assumptions={"DEBUG": True},
)
if result.complete:
    parser_input = result.source  # canonical: line numbers match the original
    # result.source_map maps output positions back to the original and to headers
else:
    for diagnostic in result.incomplete:
        print(diagnostic.location.line, diagnostic.message)
```

See [Concrete preprocessing](preprocessing.md#downstream-integration-checklist) and [Include resolution](include-resolution.md).

## Preprocess a header (`#pragma once` and include guards)

Most headers start with `#pragma once`, an include guard, or both. Run directly on such a header, cpre needs two decisions from you, because it never guesses:

```c
#pragma once
#ifndef WIDGET_H
#define WIDGET_H
#ifdef FEATURE
int f(void);
#endif
#endif
```

```bash
cpre preprocess widget.h -D FEATURE
# widget.h: line 2: condition is not determined by the current macro state: #ifndef WIDGET_H (unresolved: WIDGET_H (definedness))
# widget.h: hint: supply -D WIDGET_H or -U WIDGET_H

cpre preprocess widget.h -U WIDGET_H -D FEATURE
# widget.h: line 1: #pragma preprocessing directive is not supported during concrete preprocessing
# widget.h: hint: pass --consume-pragma PREFIX to accept pragmas you know are harmless ...

cpre preprocess widget.h --consume-pragma once -U WIDGET_H -D FEATURE --compact
# int f(void);
```

The two stops come one after the other because conditions are resolved first and pragmas are checked once the rest of the file is understood. Fix both up front:

1. **The guard.** `-U WIDGET_H` says "this is the first time the header is read", which is what a guard assumes. `--unknown-names undefined` does the same for every unset name at once.
2. **The pragma.** `--consume-pragma once` accepts `#pragma once` (in a file you name directly, it has no effect to model). Add one `--consume-pragma` per other pragma you accept, such as `pack`.

`cpre widget.h` and `cpre simplify-macros widget.h` need neither: they reason symbolically, never read pragmas, and treat the guard like any other condition. `cpre cover` needs the pragma option and finds the guard itself, returning one configuration that skips the body (`-D WIDGET_H=1`) alongside the ones that enter it:

```bash
cpre cover widget.h --consume-pragma once
```

When the header is included from a source file instead, none of this applies: with `-I`, `#pragma once` and detected guards in included headers are handled automatically ([Include resolution](include-resolution.md)).

## Check the value of a macro

Ask for the value instead of writing a probe `#if`. The result carries the expanded expression, so a precedence mistake is visible, and an unevaluable result says why.

```python
import cpre

config = cpre.MacroConfiguration(
    integers={"WIDTH": 3, "HEIGHT": 4, "PAD": 2},
    definitions=[
        cpre.MacroDefinition("AREA", "(WIDTH * HEIGHT + PAD)"),
        cpre.MacroDefinition("SUM", "WIDTH + HEIGHT"),
    ],
)

cpre.evaluate_macro("AREA", config).value  # 14
cpre.evaluate_expression("SUM * 2", config).expanded  # "3 + 4 * 2"
cpre.evaluate_expression("AREA / (PAD - 2)", config).reason
# EvaluationReason.DIVISION_BY_ZERO
```

See [Evaluating expressions and macros](evaluation.md).

## Build a configuration matrix for tests

```bash
cpre cover src/driver.c --skip-includes
```

```text
--unknown-names undefined -D DEBUG=1 -D LEVEL=2 -U SLOW -D TURBO=1
--unknown-names undefined -U DEBUG -D LEVEL=0 -U SLOW -U TURBO
# unsatisfiable: line 7: #elif: the outcome's path condition is unsatisfiable
```

Every non-comment line is a verified argument list; together they select every reachable branch outcome. Check the exit status before consuming the lines, so an incomplete result (status `2`, no configuration lines) or an uncovered outcome (status `1`, partial lines) is never mistaken for a full matrix:

```bash
matrix=$(cpre cover src/driver.c --skip-includes) || { echo "cover: incomplete or uncovered" >&2; exit 1; }
n=0
printf '%s\n' "$matrix" | grep -v '^#' | while IFS= read -r flags; do
  n=$((n+1))
  eval "cpre preprocess src/driver.c --skip-includes --compact $flags" > "out-$n.c"
done
```

Exit status `1` means some satisfiable outcome could not be covered (the `#` lines say why: typically a header fixes a macro that the generator wanted to flip, or `-I` was not given). Use `--json` for a machine-readable matrix. `cpre cover` takes the include options of `preprocess` but not `-D`/`-U`, since the configurations are its output. In Python, `cpre.cover_branches(source, skip_includes=True)` returns `configurations` and per-branch `outcomes` ([Branch-covering configurations](branch-coverage.md)).

## Analyze a file from git history or a pipe

`-` reads one source from standard input and names it `<stdin>`:

```bash
git show HEAD~5:src/config.c | cpre -
git show HEAD:src/config.c | cpre simplify-macros --diff -
```

`simplify-macros --rewrite` refuses standard input, since there is nothing to write back to; use `--diff` or `--check`. Details in [Standard input](cli.md#standard-input).

## Experiment with a Boolean expression

```bash
cpre test-input 'A && (A || B)'
cpre test-input --json '(A && B) || (A && !B)'
```

```text
Input:      A && (A || B)
Simplified: A
```

This is the quickest way to see how cpre reasons about a condition before you search a codebase for it. The JSON form has `schema` `cpre.expression/1` and the fields `input`, `simplified`, `equivalent`, `changed`, `semantics`, `symbolic_literals`.

## Embed cpre in your own analyzer

Walk a tree, analyze each file, and keep the three outcomes distinct:

```python
from pathlib import Path

import cpre


def analyze_tree(root):
    report = {}
    for path in sorted(Path(root).rglob("*.[ch]")):
        try:
            source = path.read_text(encoding="utf-8")
            result = cpre.analyze_source(source, filename=str(path))
        except (OSError, UnicodeDecodeError) as error:
            report[str(path)] = ("unreadable", str(error))
        except cpre.CpreError as error:
            report[str(path)] = ("error", error.code.value, error.location, error.message)
        else:
            if result.complete:
                report[str(path)] = (
                    "clean" if not result.findings else "findings",
                    result.findings,
                )
            elif result.partial:
                # Findings are proven, but part of the file was not analyzed.
                report[str(path)] = ("partial", result.findings, result.incomplete_components)
            else:
                report[str(path)] = ("incomplete", result.incomplete)
    return report


for name, outcome in analyze_tree(".").items():
    print(name, outcome[0])
```

Rules that keep an embedding honest:

1. Treat `complete`, `partial` and "curtailed" as three different results; only `complete` with no findings is clean.
2. Branch on `FindingKind`, `ErrorCode` and structured fields, never on message text.
3. Pass `assumptions={...}` when the build state is known, and keep `Finding.depends_on_assumptions` so you can tell configuration-independent findings from configuration-specific ones.
4. Use only names exported from the top-level `cpre` package.

The complete contract is in the [Python API guide](api.md), with the [typical setups](api.md#typical-setups) for each integration style.
