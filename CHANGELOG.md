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
- `cpre/py.typed` and the `Typing :: Typed` classifier: mypy and pyright now use
  cpre's annotations instead of treating the package as untyped.
- `SECURITY.md` with the supported versions and how to report a vulnerability
  privately, and this changelog.

### Changed
- Dependabot keeps the GitHub Actions and development dependencies current, and
  `.github/scripts/configure_repository.sh` applies the branch protection and
  security settings CONTRIBUTING describes.
- Package metadata uses a PEP 639 license expression (`Apache-2.0`) and requires
  `setuptools>=77` to build; the deprecated license table and classifier are gone.

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
