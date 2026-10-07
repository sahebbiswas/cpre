# Command-line interface

The `cpre` CLI provides symbolic analysis of C/C++ preprocessor conditions and object-like macros, a dedicated `preprocess` command for selecting one explicit concrete configuration and emitting the resulting source, and a `test-input` command for quickly evaluating and optimizing arbitrary Boolean expressions.

For Python integrations, use the [Python API guide](api.md). For concrete configuration selection and macro expansion, use the [preprocessing guide](preprocessing.md).

## Invocation

After installation, invoke cpre directly:

```bash
cpre source.c
```

The module form is equivalent:

```bash
python -m cpre source.c
```

On Windows, the Python launcher can be used as well:

```bash
py -3 -m cpre source.c
```

Use `cpre --help` for the parser-generated option summary. This guide documents the behavior and integration contracts that are not obvious from `--help` alone.

## Inputs and discovery

The CLI accepts one or more file or directory paths.

```bash
cpre src/example.c include/example.h
```

A directly named file is analyzed regardless of its filename suffix. Directory inputs require `--recursive`:

```bash
cpre --recursive src include
```

Recursive discovery includes files with these case-insensitive suffixes:

```text
.c .cc .cpp .cxx .h .hh .hpp .hxx
```

Discovery is deterministic: matching files under each directory are sorted, and the same resolved file is analyzed only once even when multiple inputs reach it.

A directory supplied without `--recursive`, a missing path, or a recursive directory with no matching C/C++ files is an invocation error. `argparse` reports the error on stderr and terminates with status `2`.

## Default text reporting

Without an output-format flag, cpre writes a human-readable report to stdout.

```bash
cpre source.c
```

The default report is filtered to branches that are dead, redundant, or simplifiable. Use `--verbose` to include unchanged branches as well:

```bash
cpre --verbose source.c
```

With multiple inputs or a directory input, the CLI uses batch presentation and prefixes emitted reports with the source path. In filtered batch mode, files with no reportable entries are omitted. `--verbose` includes those files because the full conditional tree is requested.

Human-readable text is intended for people, not as a stable machine interface. Do not parse it in downstream tooling.

## JSON output

`--json` writes the unified analysis report (both conditional directives and macro simplifications) as JSON:

```bash
cpre --json source.c
cpre --recursive --json src
```

For a single non-directory input, stdout contains an object with:
- `groups`: structural conditional-tree groups (or empty array if none/disabled). Groups of an incomplete component are not listed here.
- `macros`: array of macro analysis results, each detailing `name`, `original_replacement`, `simplified`, `simplified_replacement`, `is_equivalent`, `semantics`, and `symbolic_literals`.
- When conditional analysis is enabled, the completeness of that analysis:
  - `complete`: `true` only if every conditional was analyzed.
  - `partial`: `true` if only some independent components were left unanalyzed, so the listed `groups` are proven.
  - `incomplete`: the limit diagnostics, each with `code`, `resource`, `limit`, `observed`, `message`, `line` (or `null`), and `component` (an index into `incomplete_components`, or `null` when the whole analysis was curtailed).
  - `incomplete_components`: one object per unanalyzed component with `index`, `groups` (each with `line`, `end_line`, `directive`, `condition`), `atoms`, and `diagnostics`.

In batch mode, stdout contains an object with a `files` array; each entry includes `path` plus the `groups` and `macros` fields for that source.

The same filtering rule applies as text output: the default JSON view omits unchanged branches and unsimplified/skipped macros, while `--verbose --json` includes the full tree and all analyzed macros. In filtered batch mode, files whose filtered results have no notable entries are omitted. Files whose analysis was incomplete are always included.

JSON represents cpre's reporting model for direct inspection. For static analysis interchange, prefer SARIF. For Python-to-Python integration, prefer the top-level Python API.

## SARIF output

`--sarif` writes SARIF 2.1.0 to stdout:

```bash
cpre --recursive --sarif src > cpre.sarif
```

SARIF carries structured findings and tool notifications suitable for static-analysis ingestion. `--verbose` does not expand SARIF with unchanged branches because SARIF is findings-oriented rather than a structural tree dump.

See [SARIF output](sarif.md) for rule descriptors, fixes, notifications, and code-scanning integration.

`--json` and `--sarif` are mutually exclusive.

## CI usage

By default, findings do not make a successful analysis fail the process. Add `--fail-on-findings` when dead or redundant branches should fail CI:

```bash
cpre --fail-on-findings source.c
cpre --recursive --fail-on-findings src
```

A typical SARIF-producing CI step can keep findings in the artifact while separately choosing whether they should fail the build:

```bash
cpre --recursive --sarif src > cpre.sarif
```

If the build should fail on qualifying findings as well, include `--fail-on-findings` and preserve the SARIF file before propagating the process status.

## Exit status contract

The CLI uses these process statuses:

| Status | Meaning |
| --- | --- |
| `0` | Normal completion. Findings may exist unless `--fail-on-findings` was requested. |
| `1` | Normal completion with `--fail-on-findings`, and at least one dead or redundant branch was found. |
| `2` | Source ingestion failed, analysis/preprocessing reasoning was incomplete for a source, or command-line/path validation failed. |

Status `2` takes precedence over status `1`. If any input cannot be processed completely, the overall run is an error even if other inputs also contain findings.

## Error and incomplete-analysis behavior

Normal reports are written to stdout. Source read failures, structured cpre errors, and incomplete-analysis diagnostics are written to stderr with the source path.

The CLI reads source files as UTF-8. Unreadable files and non-UTF-8 input therefore produce stderr diagnostics and status `2`.

Malformed conditional directives and other supported `CpreError` failures are rendered on stderr with source location information when available.

ROBDD/resource-limit exhaustion (the atom limit applies per independent conditional component, the work limit to the whole source; see [Independent components and budget semantics](api.md#independent-components-and-budget-semantics)) and other `AnalysisResult.complete == False` cases are also treated as incomplete processing: diagnostics are written to stderr and the process exits with status `2`. cpre never treats an incomplete source as a clean source merely because it has no findings.

When only some independent components exceed a limit, the result is partial (see [Partial results and incomplete components](api.md#partial-results-and-incomplete-components)). The report still shows the proven findings for the rest of the file, followed by a separate section that lists each unanalyzed conditional:

```text
2: #if A [redundant]
  reason: condition is always true in this branch context
  ...
Incomplete analysis (no findings are reported for these conditionals):
  6-8: #if W_0 || W_1 || ... [incomplete: analysis limit exceeded for atoms: 70 > 64]
```

stderr names the same conditionals (`source.c: line 6: conditional at lines 6-8 not analyzed (analysis limit exceeded for atoms: 70 > 64); findings cover only the rest of the file`), and the exit status is still `2`.

When `--sarif` is active, source/tool errors are also represented as SARIF tool notifications where applicable, while the process still exits with status `2`.

## Unified analysis flow

`cpre` analyzes both C/C++ conditional directives (`#if`, `#elif`) and eligible object-like macro definitions (`#define`) in a single pass:

```bash
cpre source.c
cpre --recursive src/
cpre --fail-on-findings src/
cpre --json source.c
```

In default mode, notable conditional branches (dead, redundant, simplifiable) and simplified macro definitions are reported:

```text
line 12: #elif A [dead]
  reason: condition contradicts its parent or earlier branches
line 45: #define FEAT_1 (0 && A) || B -> (B)
```

Use `--verbose` to include unchanged branches, unsimplified macros, and skipped non-candidate definitions.

### Symbolic-literal mode

By default, integer constants `0` and `1` retain ordinary C truth values (`0` = false). To treat selected literals as symbolic Boolean atoms (preserving disabled control switches such as `(0 && A) || B`), pass `--symbolic-literal`:

```bash
cpre --symbolic-literal 0 source.c
```

`--symbolic-zero` is a convenient shorthand for `--symbolic-literal 0`:

```bash
cpre --symbolic-zero source.c
```

Currently only `0` is supported as a symbolic literal. Passing an unsupported value terminates with an error and status `2`.

See [Macro Boolean simplification](macro-simplification.md) for full semantic details and warnings.

### Disabling specific analyses

If an integration requires checking only conditional branches or only macro definitions, use the opt-out flags:

- `--no-macros`: Disables macro Boolean simplification; only conditional directives are analyzed.
- `--no-conditionals`: Disables conditional directive analysis; only macro definitions are analyzed.

## Concrete preprocessing

Use the dedicated `preprocess` command when a downstream tool needs one concretely selected source representation:

```bash
cpre preprocess source.c
cpre preprocess source.c --define FEATURE
cpre preprocess source.c -D LEVEL=2 -U DISABLED
cpre preprocess source.c --standard-macro __STDC__=1
```

The command is deliberately explicit and does not infer compiler or build state from the host. `-D NAME` creates an empty object-like macro, while `-D NAME=VALUE` supplies explicit replacement text. `-U NAME` records an explicit undefined macro. `--unknown-names undefined` opts into closed-world handling for names absent from the supplied configuration; the default is `open`.

Canonical coordinate-preserving output is the default and corresponds to `PreprocessResult.source`. Use `--compact` only when a presentation-oriented view is desired:

```bash
cpre preprocess source.c --define FEATURE --compact
cpre preprocess source.c --compact --max-blank-lines 1
```

Incomplete preprocessing never emits partial transformed source. Diagnostics are written to stderr and the command exits with status `2`. A successful preprocessing run exits `0`; this command does not use findings-oriented `--fail-on-findings` semantics.

`cpre preprocess` currently accepts one source file. Directory/batch processing is intentionally not inherited from the symbolic analysis CLI. Reachable `#include` directives, unsupported pragmas, unresolved `__has_include` queries, and other constructs outside the documented concrete-preprocessing contract remain governed by the library's atomic incomplete-result behavior.

For the full transformation, configuration, mapping, deterministic-context, and host-callback contract, see [Concrete preprocessing](preprocessing.md) and [Concrete macro configuration](concrete-configuration.md). For canonical versus compact output semantics, see [Compact preprocessing output](compact-preprocessing.md).

## Dedicated macro simplification and rewrite workflow

For dedicated macro inspection and in-place source rewriting, `cpre` provides the `simplify-macros` command (with `analyze-macros` as an alias):

```bash
# Report-only mode: inspect simplifications without modifying source files
cpre simplify-macros source.c
cpre analyze-macros source.c

# Explicit rewrite mode: apply only proven-equivalent simplifications in-place
cpre simplify-macros --rewrite source.c
cpre simplify-macros --in-place source.c
```

### Report format

In report mode, each simplified definition distinguishes the original replacement list, simplified replacement list, proof/equivalence status, and source location:

```text
line 12: #define FEAT_1 (0 && A) || B -> (B) [proven equivalent]
```

With `--verbose`, unsimplified candidates and skipped non-candidate definitions are also displayed with diagnostic reasons:

```text
line 4: #define BUFFER_SIZE 1024 skipped: non-Boolean integer literal: '1024'
line 8: #define FEAT_2 (A || B) (expression is already in simplest equivalent form)
```

In rewrite mode (`--rewrite`), successfully applied transformations are indicated:

```text
line 12: #define FEAT_1 (0 && A) || B -> (B) [rewritten, proven equivalent]
```

### Safety and verification guarantees

- **Report-only by default**: reporting never modifies source files. Source mutation requires the explicit `--rewrite` (or `--in-place`) flag.
- **Proof-verified transformations**: only transformations proven equivalent under the selected semantic mode are applied.
- **Reparsing and re-analysis verification**: after rewriting in-place, the resulting definitions are re-parsed and re-analyzed to verify that they parse cleanly, reached the simplest form, and remain semantically equivalent. If verification fails, the rewrite is aborted and reported with exit status `2`.
- **Formatting and comment preservation**: rewriting operates on exact replacement ranges; leading directive whitespace, macro names, inline comments (`/* ... */` and `// ...`), unrelated code, and original line endings (CRLF / LF) are preserved.
- **Incomplete expressions protected**: macros that exceed analysis resource limits or contain unmodeled constructs are never emitted as rewrites.

### Symbolic-literal mode in rewrites

Symbolic-literal options are supported in both report and rewrite modes:

```bash
# Preserve disabled switches such as (0 && A) || B
cpre simplify-macros --symbolic-zero source.c
cpre simplify-macros --rewrite --symbolic-zero source.c
```

### JSON output for macro workflows

Passing `--json` to `simplify-macros` produces structured machine-readable results:

```bash
cpre simplify-macros --json source.c
cpre simplify-macros --rewrite --json source.c
```

The output contains `path`, `rewritten`, `applied_count`, `verified`, `semantics`, `symbolic_literals`, and the array of analyzed `macros`.

## Arbitrary Boolean expression testing with test-input

The `cpre test-input` subcommand accepts an arbitrary Boolean macro equation or expression, runs it through cpre's Boolean parser and ROBDD simplification machinery, and prints the optimized equivalent expression:

```bash
cpre test-input 'A && (A || B)'
```

Output:

```text
Input:      A && (A || B)
Simplified: A
```

The command serves two primary workflows:

1. **Quick experimentation** — test prospective Boolean macro equations without creating a C/C++ source file containing a `#define`.
2. **Engine-level testing** — provide a convenient black-box CLI interface for exercising ROBDD simplification identities, absorption, contradiction, tautology, and regression cases.

### Examples

Representative Boolean identities and simplifications:

```bash
# Identity and absorption
cpre test-input 'A && (A || B)'
cpre test-input '(A || B) && A'
cpre test-input '(!A && B) || A'

# Factoring and consensus
cpre test-input '(A && B) || (A && !B)'
cpre test-input '(!A || B) && (A || B)'
cpre test-input '(A && B) || (A && C)'

# Contradiction and tautology
cpre test-input 'A && !A'
cpre test-input 'A || !A'
```

### Expression semantics

`cpre test-input` uses the existing Boolean expression parser and semantics:

- Identifiers as Boolean atoms (`A`, `FEATURE_ENABLED`)
- `!` for negation
- `&&` for conjunction
- `||` for disjunction
- Parentheses for grouping
- Integer/Boolean constants (`0`, `1`, `0x1`) according to parser semantics

It does not read or preprocess C/C++ source files, nor evaluate arbitrary C expressions.

### Symbolic-literal mode

By default, integer literals like `0` are evaluated as fixed C truth values (e.g. `A && 0` reduces to `0`). Use `--symbolic-zero` or `--symbolic-literal 0` to treat `0` as a free Boolean atom:

```bash
# Ordinary semantics: reduces to 0
cpre test-input 'A && 0'

# Symbolic-zero semantics: preserves the atom
cpre test-input --symbolic-zero 'A && 0'

# Symbolic-zero simplification
cpre test-input --symbolic-zero '(A && 0) || (A && !0)'
```

### Machine-readable JSON output

`--json` emits the simplified result as a documented JSON object:

```bash
cpre test-input --json 'A && (A || B)'
```

Output:

```json
{
  "input": "A && (A || B)",
  "simplified": "A",
  "equivalent": true,
  "changed": true,
  "semantics": "ordinary",
  "symbolic_literals": []
}
```

JSON fields:

| Field | Type | Description |
| --- | --- | --- |
| `input` | string | Original input expression text supplied on the command line. |
| `simplified` | string | Optimized equivalent Boolean expression. |
| `equivalent` | boolean | `true` when the simplified expression is proven equivalent via ROBDD. |
| `changed` | boolean | `true` if the expression simplified to an algebraically different form, `false` if already in simplest equivalent form. |
| `semantics` | string | `"ordinary"` or `"symbolic-literal"`. |
| `symbolic_literals` | array of integer | List of integer literals treated as symbolic atoms (e.g. `[0]`). |

Internal BDD details (node counts, IDs, variable orderings) are intentionally not exposed in the schema.

### Resource limits and error handling

`cpre test-input` preserves bounded ROBDD execution:

- `--max-atoms N`: Maximum number of distinct Boolean atoms (default: 64).
- `--max-bdd-nodes N`: Maximum number of BDD nodes before aborting (default: 100,000).
- `--max-work N`: Maximum deterministic BDD operations before aborting (default: 500,000).

Malformed expressions, unmatched parentheses, missing operands, unsupported syntax, or resource exhaustion write a concise diagnostic to stderr and exit with status `2`:

```bash
cpre test-input 'A &&'
# Stderr: cpre test-input: error: expected an operand after '&&' at column 3
# Exit code: 2
```

An incomplete ROBDD computation is never reported as a successful simplification.

## Full option reference

```text
--recursive
    Recursively discover C/C++ source files under directory inputs.

--json
    Write structural conditional trees and macro analysis results as JSON.

--sarif
    Write conditional findings and tool notifications as SARIF 2.1.0.

--verbose
    Include unchanged conditional branches and unsimplified/skipped macros.

--fail-on-findings
    Exit with status 1 when a dead, redundant, or simplifiable branch or macro is found,
    provided no status-2 error occurred.

--symbolic-literal N
    Treat integer literal N as a symbolic Boolean atom in macro analysis (e.g. 0).

--symbolic-zero
    Convenience shorthand for --symbolic-literal 0.

--no-macros
    Disable macro Boolean simplification analysis.

--no-conditionals
    Disable conditional directive analysis.
```

`--json` and `--sarif` cannot be used together.

## Choosing an integration surface

Use the CLI for shell/CI workflows. Use SARIF when findings need to cross a process or analysis-system boundary. Use `cpre.analyze_source()` when the caller is Python and needs structured findings or edits. Use `cpre.preprocess_source()` when the caller needs one concretely selected, macro-expanded source representation for a downstream parser or analyzer.
