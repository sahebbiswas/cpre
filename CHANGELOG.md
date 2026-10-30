# Changelog

All notable changes to cpre are recorded here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/).

Every pull request merged to `main` bumps the version, but only some versions are
published to PyPI (see [Releasing](CONTRIBUTING.md#releasing)). Changes land under
**Unreleased** and move under a version heading when that version is released.
**Behaviour change** marks anything users can notice: different results, newly
rejected input, removed or renamed options. Incompatible changes to a JSON document
raise that document's schema major version and are listed here (see the
[JSON contract](docs/json-contract.md#compatibility-and-versioning)).

## [Unreleased]

### Added
- Progress for a single slow file: `--expand-macros` on a large file no longer runs silently. After
  about half a second a live `macros done/total` line (or, in a log, a line every ten seconds)
  names the macro being analysed.
- Recomposition stage for macro simplification. Other atom orders and forms folded back into the
  referenced macros are tried, the shortest proven form is chosen, and a suggestion is reported only
  if it has fewer tokens than the definition.

### Changed
- **Behaviour change:** macro suggestions that were not shorter than their definition are no longer
  reported (the macro is reported as already simplest), and some are replaced by shorter forms,
  including forms naming a referenced macro with `--expand-macros` (report-only).
- **Behaviour change:** text reports show a multi-line or indented macro replacement or `#if`
  directive on one line with whitespace runs collapsed (string and character literals untouched).
  JSON, diffs and rewrites keep the source exactly.

### Stability
- **cpre is 1.0.** The development status is Production/Stable and the documented
  surface now follows Semantic Versioning: the top-level Python API, the JSON
  documents, SARIF output, documented CLI options and exit statuses. The new
  [What 1.0 promises](docs/downstream-compatibility.md#what-10-promises) section lists
  what is covered and what is not (text output, telemetry output, internal modules,
  default resource limits). Nothing in the documented surface changed for 1.0
  itself.

### Added
- Macro simplification can see through other macros. `analyze_macros(..., expand_macros=True)`,
  `simplify_macros(..., expand_macros=True)`, `analyze_macro(..., expand_macros={name: definition})`
  and `--expand-macros` on `cpre` and `cpre simplify-macros` replace an identifier that names a
  Boolean macro defined in the same source by that macro's replacement before simplifying, so
  `#define D (A && C)` with `#define A (B || C)` reports `(!!C)`. Only Boolean candidates (and bare
  identifier aliases of them) are expanded; a plain constant such as `#define ENABLED 1`, a
  function-like macro or any other non-candidate stays an opaque atom, because its value depends on
  the build configuration. A macro is expanded only when it has one `#define`, no `#undef`, comes
  before its user and sits in a conditional block that also encloses the user; cycles and
  over-deep macro nesting stay atoms, and `reason` says why; an expanded expression nested
  more than 256 levels deep is reported as incomplete (`nesting`). The result is reported but never rewritable,
  so combining the option with `--rewrite`, `--in-place`, `--diff` or `--check`, or
  `simplify_macros(rewrite=True, expand_macros=True)`, is an error (usage status `2` on the
  command line, `AnalysisError` with `ErrorCode.INVALID_CONFIGURATION` in Python), as is
  `--expand-macros` with `--no-macros` or `--sarif`. A macro whose replacement has a top-level
  `&&` or `||` outside parentheses is not expanded, because the preprocessor would bind it with
  the operators around each use. New `MacroAnalysisResult`
  fields: `expanded_replacement`, `expanded_from`, `expanded_expression`, `expanded`. JSON macro
  records gain `expanded_from` and `expanded_replacement`, and the file record gains
  `expand_macros`, only when the option was given, so default output is unchanged
  ([#233](https://github.com/sahebbiswas/cpre/issues/233)).
- Evaluated values are checked against GCC and Clang. `tests/test_evaluation_differential.py`
  runs a `#if (EXPR) == VALUE` probe, plus a signedness probe, for every expression cpre
  reports as concrete: operator, boundary, unsigned and macro fixtures and a seeded random
  generator (rerun a failure with `CPRE_DIFFERENTIAL_SEED`). The one difference found, GCC
  typing a skipped division by an unsigned zero as signed, is recorded in
  `known_differences.json` ([#228](https://github.com/sahebbiswas/cpre/issues/228)).
- `evaluate_expression(..., trace=True)`, `evaluate_macro(..., trace=True)` and
  `cpre evaluate --explain` show how a value was derived (`Evaluation.trace`, a `Trace` of
  `TraceStep` and `TraceKind`): each macro expansion in order, a warning when an
  unparenthesized replacement regroups with the operators around its use (`SUM` as
  `W + H` in `SUM * 2`), a note when a signed operand becomes unsigned, each evaluated
  operation with its result, and where evaluation stopped. The trace holds at most 200
  steps and reports truncation. The `cpre.evaluation/1` record gains an optional `trace`
  (a compatible addition, absent unless requested). The precedence test is shared with
  `simplify-macros` ([#222](https://github.com/sahebbiswas/cpre/issues/222)).
- **Behaviour change:** an expression whose only obstacle is a name the configuration
  does not settle now has a **symbolic** result (`EvaluationState.SYMBOLIC`) instead of
  being unevaluable, and `cpre evaluate` exits `3` for it instead of `1`:
  `Evaluation.expanded` is the expression with every settled macro substituted and the
  unresolved names left in place, and the new `Evaluation.unresolved` lists them. It is
  never folded, and any other obstacle still wins: an unresolved name beside a division
  by a certain zero, an overflow, unsupported syntax or an implementation-defined
  character is unevaluable with that reason. `cpre evaluate` prints a `~` line and the
  names and exits `3` when an item is symbolic and none is unevaluable (`1` still wins).
  The `cpre.evaluation/1` record gains `unresolved` and the `symbolic` state, which are
  compatible additions, so the schema version is unchanged
  ([#221](https://github.com/sahebbiswas/cpre/issues/221)).
- `evaluate_expression()` and `evaluate_macro()` compute the integer value of an
  expression, or of a macro that expands to integer arithmetic, under a
  `MacroConfiguration`, with the same evaluator as `#if`. They return an immutable
  `Evaluation` (`EvaluationState`, `EvaluationReason`) with the expanded expression and,
  when no value can be proved, a stable reason instead of a guess or an exception. A
  `cpre.evaluation/1` JSON record is documented in the
  [JSON contract](docs/json-contract.md#cpreevaluation1), and the new
  [Evaluating expressions and macros](docs/evaluation.md) guide covers the semantics.
- `cpre evaluate`, a command that prints the integer value of each macro or expression
  named on the command line under one concrete configuration (`-D`, `-U`,
  `--config-from`, `--unknown-names` and the other `preprocess` configuration options),
  with the expanded expression and a reason for any item that has no provable value. It
  exits `1` when any item has none, and `--json` writes a `cpre.evaluations/1`
  document. Control characters in an operand or its expansion are shown as `\xNN` escapes
  in the text report, never written to the terminal. See the
  [CLI reference](docs/cli.md#macro-and-expression-values-with-evaluate).
- `cpre/py.typed` and the `Typing :: Typed` classifier: mypy and pyright now use
  cpre's annotations instead of treating the package as untyped.
- `SECURITY.md` with the supported versions and how to report a vulnerability
  privately, and this changelog.

### Changed
- **Behaviour change:** an unresolved name, or a `defined NAME` the configuration does
  not settle, no longer makes a condition or an evaluation unresolved when it is the
  whole of an operand that `&&`, `||` or `?:` does not evaluate. `1 || UNKNOWN`,
  `0 && UNKNOWN`, `1 ? 2 : UNKNOWN` and `0 ? UNKNOWN : 3` now have a value, as in GCC and
  Clang; `UNKNOWN || 1` and a name that is only part of an unevaluated operand
  (`1 || UNKNOWN + 1`) stay unresolved
  ([#224](https://github.com/sahebbiswas/cpre/issues/224)). The result's expanded text is
  now also kept when the unresolved operand is a `defined NAME`.
- **Behaviour change:** the minimum supported Python is now 3.10. CPython 3.9 reached
  end of life in October 2025; cpre drops it and follows CPython's lifecycle from
  here on (see [Versioning](CONTRIBUTING.md#versioning)). Installing on 3.9 is
  refused by `requires-python`; earlier releases keep working there.
- Dependabot keeps the GitHub Actions and development dependencies current, and
  `.github/scripts/configure_repository.sh` applies the branch protection and
  security settings CONTRIBUTING describes.
- Package metadata uses a PEP 639 license expression (`Apache-2.0`) and requires
  `setuptools>=77` to build; the deprecated license table and classifier are gone.

### Fixed
- **Behaviour change:** a condition that depends on an implementation-defined character
  constant (a multicharacter constant, or a constant such as `'a'` compared with another
  constant) is now reported at that constant. It used to be reported at whichever later
  token the parser had reached, for example column 12 instead of column 5 in
  `#if 'ab' + 1`. Which conditions are unresolved is unchanged.

## [0.42.0] - 2026-10-22

### Added
- `--color auto|always|never` and `--progress auto|always|never` for every source
  command. Progress for multi-file and recursive runs is a live status line on
  stderr (or plain lines in logs), with a size-weighted ETA.
- A closing `Summary` line in text reports.

### Changed
- **Behaviour change:** the text report was redesigned: status badges (`DEAD`,
  `REDUNDANT`, `SIMPLIFY`, `REWRITTEN`, `INCOMPLETE`, `ok`), a `-`/`+` pair for every
  transformation with the changed tokens emphasized, and one color meaning per role.
  The layout is the same with and without color. Text output was never a stable
  interface; JSON and SARIF are unchanged.
- **Behaviour change:** text reports for several files are written as each file
  finishes instead of at the end, so output starts at once and survives an interrupt.
- **Behaviour change:** an interrupted run (Ctrl-C) prints how far it got and exits
  with status 130.

## [0.41.0] - 2026-10-20

### Added
- `--symbolic-literal 1`, joining `0`: the literal `1` can be kept as a free atom
  when simplifying macros. The option is repeatable.

### Removed
- **Behaviour change:** the `--symbolic-zero` and `--symbolic-one` shorthand options.
  Use `--symbolic-literal 0` and `--symbolic-literal 1`.

## [0.40.1] - 2026-10-19

### Fixed
- The installed-package test failure that blocked the 0.40.0 release.

## 0.16.0 to 0.40.0

These versions were not published to PyPI individually. Their changes, by
theme (pull requests #95 to #200 carry the details):

- **JSON contract.** Every `--json` document carries a `schema` identifier
  (`cpre.<kind>/<major>`); `simplify-macros --check` and `--diff` accept `--json`;
  `--telemetry` adds opt-in analysis resource figures.
- **New commands and options.** `cpre cover` (verified branch-covering
  configurations), `preprocess --dump-macros`, `--config-exclude`, `--has-include`,
  `--consume-pragma`, `-` for standard input, `--version`, and CLI resource-limit
  options.
- **Macro simplification.** `simplify-macros --diff` and `--check`; rewrites
  preserve meaning at every use of a macro; property-based round-trip tests.
- **Preprocessing correctness.** One shared line model and lexical front end;
  directives report the line of their `#`; invalid conditions report the physical
  position of the offending token; variadic calls that omit the variadic argument,
  stringification spacing, self-referential macros in `#if`, and directives split by
  multi-line comments were fixed; a GCC/Clang differential harness checks concrete
  preprocessing.
- **Internals.** Option and result types moved below the core, the CLI became a
  package of per-command modules, and every module is type checked.

## Earlier releases

Releases `0.1.0` to `0.15.0` are described in the
[GitHub releases](https://github.com/sahebbiswas/cpre/releases) and the git history.
`0.7.0` marked the transition from Alpha to Beta.

[Unreleased]: https://github.com/sahebbiswas/cpre/compare/v0.42.0...HEAD
[0.42.0]: https://github.com/sahebbiswas/cpre/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/sahebbiswas/cpre/compare/v0.40.1...v0.41.0
[0.40.1]: https://github.com/sahebbiswas/cpre/releases/tag/v0.40.1
