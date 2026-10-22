# Contributing to `cpre`

Thank you for contributing! Here are some instructions to get your local development environment set up and ensure your code is ready to be merged. [Every pull request](#every-pull-request) covers what each pull request must include, [Reporting gaps](#reporting-gaps) covers issues you find along the way, and [Versioning](#versioning) and [Releasing](#releasing) cover how versions and releases work.

## Prerequisites

- **Python 3.10+**
- Git

## Setup

The simplest way to set up the repository is to install the package in editable mode with development dependencies.

```bash
git clone https://github.com/sahebbiswas/cpre.git
cd cpre

# It is highly recommended to use a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies and the package itself
pip install -e ".[dev]"
```

If you prefer faster dependency management, using [`uv`](https://docs.astral.sh/uv/) is fully supported and recommended:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Pre-commit Hooks

This repository uses [pre-commit](https://pre-commit.com/) to ensure code quality and formatting standard.

```bash
pre-commit install
```

Now, every time you commit, `ruff` (for linting and formatting) and `mypy` (for type checking) will automatically run and verify your changes.

## Running Tests and Linting

We use `tox` to run checks consistently across all supported Python versions.

- **Run all tests** across installed Python versions:
  ```bash
  tox run
  ```

- **Run linting and type checking**:
  ```bash
  tox run -e lint,mypy
  ```

- **Run tests interactively** with `pytest`:
  ```bash
  pytest
  ```

- **Search deeper with the property-based tests.** The Hypothesis tests run a
  small, derandomized number of examples so CI stays fast. Raise it locally
  when changing macro simplification or rewriting:
  ```bash
  CPRE_PROPERTY_EXAMPLES=2000 pytest tests/test_macro_simplification_properties.py
  ```
  Keep every regression the search finds as an explicit `@example`.

- **Compare with GCC and Clang.** `tests/test_compiler_differential.py`
  preprocesses every case in `tests/compiler_differential/cases` with each
  compiler found on `PATH` and with cpre, and fails on any difference that is
  not recorded. When changing concrete preprocessing, add a case for the
  construct you touched. See
  [Compiler differential tests](docs/compiler-differential.md).

## Code Style

- **Formatting & Linting**: We use `ruff`.
- **Typing**: We use `mypy`. All new code should be fully typed and pass `mypy` with `--strict`.

When opening a Pull Request, our CI will automatically verify these checks.

## Module layering

The package has one direction of dependency:

- `cpre/__init__.py` and `cpre/api.py` are the public entry points. `cpre/cpre.py` is an internal compatibility facade for script mode and historical callers. `cpre/cli/` is the command line front end. These sit above everything else.
- Every other module is the core. It must not import `cpre.api` or `cpre.cpre` (`tests/test_import_layering.py` fails if it does), and `cpre.api` does not import `cpre.cpre`.
- The option, result and diagnostic types (`AnalysisOptions`, `MacroAssumptions`, `AnalysisIncomplete`, `Finding`, `AnalysisResult` and the parse-error translation) are defined in `cpre/_types.py`, below the core, and re-exported unchanged by `cpre.api` and `cpre`. Import them from `._types` inside the package. They report `cpre.api` as their `__module__`.
- Concrete preprocessing has one core entry point, `preprocessing.preprocess_request(PreprocessRequest)`. `cpre.preprocessing.preprocess_source` (also importable as `cpre.include_queries.preprocess_source`) and `cpre.pragmas.preprocess_source` are thin adapters that build a `PreprocessRequest`; the pragma layer wraps the core call to deliver pragmas, and `MacroConfiguration.from_source` passes `protected_names` in the request for seed evaluation. Add new behavior as a request field, not as a keyword argument threaded through the wrappers, and do not rebind module attributes.
- One source's preprocessing (the primary source or an include) is `_preprocess_unit`, which builds a `_UnitContext` and hands it to `_UnitProcessor`. The pieces are separate classes in `cpre/preprocessing.py` that can be built and tested alone: `_BranchSelector` (Boolean check, then numeric evaluation, then `__has_include` retry, then the unresolved diagnostic), `_QueryAnswerer`, `_IncludeHandler`, and the `#define` / `#undef` / `#line` handling in `_UnitProcessor`. Read-only macro access is typed with the `MacroLookup` protocol in `cpre/macros.py`, so wrappers such as the condition view need no `type: ignore`. `cpre.preprocessing` is type checked.
- Macro expansion is a pipeline of stages, each a private module with explicit inputs and outputs, behind the `Expansion` facade in `cpre/expansion.py` (whose external surface is `Expansion`, `ExpansionError`, `SourceMapping` and `tokenize`): `_replacement` (tokenize a replacement list, lift `__VA_OPT__` groups), `_arguments` (collect an invocation's arguments and bind them to parameters, including the variadic tail), `_substitution` (`#`, `##`, `__VA_OPT__` and parameter replacement; `#` spaces its operand by each token's `space_before`, so keep that flag correct when a stage creates or moves tokens), `_rescan` (argument prescans on an explicit trampoline, then the rescan) and `_expansion_output` (merge generated tokens into edits, render source mappings). A fix to one rule, for example stringification or an omitted variadic argument, belongs in one stage; `tests/test_expansion_stages.py` tests each stage on its own. No function in these modules is longer than 80 lines and they are all type checked.
- The command line is the package `cpre/cli/`: `main` in `cpre/cli/__init__.py` only dispatches on the first argument, and each command is its own module (`analyze` for the default command, `simplify_macros`, `preprocess` with `preprocess_configuration` and `has_include_answers`, and `boolean_expression` for `test-input`), with exit statuses and the options the commands share in `_common`. No command module is longer than 400 lines and the package is type checked. A new command is a new module plus one line in `main`; a new flag goes in that command's parser, and `docs/cli.md` documents it.
- What the commands print is built outside the package so other front ends can reuse it: `cpre/serialization.py` returns the JSON-ready dictionaries (`build_file_json`, `preprocess_document`, ...) and `cpre/text_reports.py` returns the text (diagnostic lines, macro and combined reports, unified diffs). Neither prints, exits or imports a command module, so changes to an output format belong there, not in a command. Conditional-tree text and JSON stay in `cpre/reporting.py`. Color roles, glyphs, terminal detection and the token-level diff highlighting they share live in `cpre/_style.py`, which every text builder takes as a `Style`; a new kind of entry gets a badge and a role there, and one meaning per color (see [Color](docs/cli.md#color)). Progress for multi-file runs is the one piece of terminal behavior that belongs to the command line: `cpre/cli/_progress.py` writes only to stderr and wraps anything else a command prints in `paused()`, while the wording of the display is pure functions of a `Snapshot` in `cpre/cli/_progress_text.py`.
- `cpre/lexer.py` is the single lexical front end (see [the shared line model](docs/api.md#suggested-edits-and-source-ranges)).

Two function-level imports remain on purpose, each marked in the code: `configuration.MacroConfiguration.from_source` runs the preprocessor, and the preprocessor imports `configuration`; `unknown_macros` is imported by `preprocessing` and needs one of its helpers back.

## Every pull request

- **Bump the version.** Each pull request merged to `main` changes
  `__version__` in [`cpre/__init__.py`](cpre/__init__.py) exactly once (see
  [Versioning](#versioning)).
- **Update the docs** (README, `docs/`, this file) in the same pull request as
  the code they describe.
- **Update the changelog.** Add a line under **Unreleased** in
  [`CHANGELOG.md`](CHANGELOG.md) for any change users can notice, under Added,
  Changed, Deprecated, Removed, Fixed or Security. Start a change that alters
  results, rejects input that used to be accepted, or removes or renames an
  option with **Behaviour change:**. Releasing moves **Unreleased** under the new
  version heading (see [Releasing](#releasing)).
- **Describe the change in the pull request.** The pull request description is
  the record of what changed and why. Repeat **Behaviour change:** there for the
  same changes.
- **Close the issues it finishes.** Put `Closes #N` in the pull request
  description, one per line, for each issue the pull request completes, so
  GitHub links them and closes them when it merges. Naming the issue (`#N`)
  in the title, or anywhere without a closing keyword, does not close it. A
  closing keyword in a commit message does close it on merge, but does not
  link the pull request, so use the description. Use `Part of #N` or
  `Refs #N` for an issue the pull request only advances.
- **Gaps you find but don't fix** are tracked in an issue and linked from the
  pull request. See [Reporting gaps](#reporting-gaps) for how to find or create
  that issue.

## Reporting gaps

During development you will notice bugs, edge cases, missing features and
documentation gaps that are outside the change you are making. Record each one
in the issue tracker instead of leaving it in a code comment or a pull request
thread, and don't create duplicates:

1. **Search existing issues first.** Search open *and* closed issues for the
   affected function, CLI option, error message, and `ErrorCode`, and for the
   issue the work started from. Read the likely matches, not just their
   titles.
2. **If an issue already covers it, add to that issue.** Comment with what is
   new: a minimal reproduction (source text plus the exact API call or `cpre`
   command, with its actual and expected output), the version or commit you
   used, and the pull request or issue where you found it. Don't open a second
   issue for the same problem.
3. **If an issue is related but the gap is substantially different**, for
   example another construct, another code path, or a fix that would be
   reviewed separately, create a new issue and add it as a
   [sub-issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
   of the related one, or link it with "Follow-up from #N" when a sub-issue
   doesn't fit. If a closed issue matches but the problem is back, open a new
   issue that links to the closed one rather than commenting on it.
4. **If nothing matches, create a new issue.** Give it:
   - a title with a priority prefix in the style of the existing issues
     (`[P2] ...`, where a lower number is more urgent);
   - **Problem**: what is wrong or missing, and where you found it;
   - a minimal reproduction with actual and expected results, for a bug;
   - **Scope** or **Options**, when there is more than one reasonable fix;
   - **Acceptance criteria** that a pull request can be checked against.
5. **Link it from the pull request** with `Refs #N` (not `Closes #N`, which
   would close it on merge), so reviewers can see what was deferred.

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). The
version moves with `main`: every merged pull request gets its own version, and
releases are cut from `main` whenever it is stable enough (see
[Releasing](#releasing)). A version is never held back waiting for a release.

- The version is defined once, as `__version__` in `cpre/__init__.py`.
  `pyproject.toml` reads it, `cpre.__version__` exposes it, and SARIF output
  reports it. Don't repeat the number elsewhere.
- Bump it once per pull request, against the version on `main`, not once per
  commit. If `main` moves to the same version before you merge, bump again on
  top of it.
- CI enforces this. The *Version bumped* job checks that the pull request's
  version is exactly one step above the version on `main`: patch + 1, minor + 1
  with patch 0, or major + 1 with the rest 0. It reads the `main` version from
  the first parent of GitHub's test merge commit, so it needs no stored copy
  and no extra fetch. Whether the step is the right one is left to review.
- GitHub computes that merge commit when the check runs and does not re-run the
  check when `main` moves. To stop two pull requests from merging with the same
  version, `main` is protected with *Require status checks to pass* (including
  *Version bumped*) and *Require branches to be up to date before merging*; see
  [Repository settings](#repository-settings).
- Pick the part to bump by the largest change in the pull request:

  | Bump | When | Example |
  |------|------|---------|
  | Patch (`0.20.0` → `0.20.1`) | Bug fixes, docs, tests, internal refactors, tooling and CI | Fix a wrong simplification of a nested conditional |
  | Minor (`0.20.1` → `0.21.0`) | A new feature or capability: a new public function, field or CLI option, newly supported preprocessor construct | Add a new CLI subcommand |
  | Major (`1.x` → `2.0.0`) | An incompatible change to the public API contract | Rename or remove a public function or CLI option |

- Get the maintainer's agreement before a major bump.
- Supported Python versions follow CPython's lifecycle: a version is supported until
  it reaches end of life, and the release after that drops it. Dropping a Python
  version is announced as a **Behaviour change:** and is a minor bump (`1.x`), not a
  major one, because the dropped interpreter is already unsupported upstream.
  Python 3.10 reaches end of life on 2026-10-31 (PEP 619), so it stays the floor until
  then and is dropped in the first minor release after that date.
- A change that breaks the documented contract is a major bump, and is announced as
  a **Behaviour change:** in the pull request and the changelog. One example is
  rejecting input that used to be accepted. What the contract covers is listed in
  [What 1.0 promises](docs/downstream-compatibility.md#what-10-promises); anything
  outside it (text output, telemetry, internal modules) can change in a minor or
  patch release. See [Downstream compatibility](docs/downstream-compatibility.md#compatibility-and-versioning).
- The public API is the documented top-level `cpre` package (see
  [Downstream compatibility](docs/downstream-compatibility.md)) and the `cpre`
  CLI. Submodules such as `cpre.model` or `cpre.expressions`, and modules whose
  names start with `_`, are internal and can change in any version.
- Most versions are never published. PyPI only gets the versions that are
  released, so gaps between published versions are expected.

## Repository settings

A few settings live on GitHub rather than in the repository. A maintainer with admin
rights applies them with the GitHub CLI:

```bash
.github/scripts/configure_repository.sh --dry-run   # show what would be sent
.github/scripts/configure_repository.sh             # apply
```

Run it signed in to the GitHub CLI (`gh auth login`) as a repository admin: the settings
need admin rights, and the default workflow token of CI cannot change them. Without
`--dry-run` it changes the repository for real (branch protection, Dependabot, alerts,
private reporting), so read the dry run first. Branch protection is described in
[GitHub's documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

It protects `main` (required status checks: every job of the CI workflow plus
*Version bumped*, branch up to date before merging; a new protection also forbids
force pushes and deletion), and turns on Dependabot security updates, dependency
alerts and private vulnerability reporting (used by [SECURITY.md](SECURITY.md)). Run
it again after the CI job names change, for example when the Python version matrix
changes: on a branch that is already protected it updates only the required status
checks and leaves every other rule (reviews, push restrictions, admin enforcement)
as it is.
`.github/dependabot.yml` keeps the GitHub Actions and development dependencies
current; cpre itself has no runtime dependencies.

## Releasing

A release publishes whatever version `main` is at. Releases go to PyPI through
[`release.yml`](.github/workflows/release.yml), which uses PyPI Trusted
Publishing (no API tokens are stored).

1. Pick a commit on `main` that is green and stable. Its `cpre/__init__.py` is
   the version released, so no version change is needed.
2. Optional dry run: manual runs of the workflow (Actions → *Publish to PyPI /
   TestPyPI* → *Run workflow*) upload only to TestPyPI, but a version can be
   uploaded only once per index, and a release fails if its version is already
   on TestPyPI. So never run it on `main` itself. Instead, push a temporary
   branch from the chosen commit that changes `__version__` to a pre-release of
   the version (`0.20.2` → `0.20.2rc1`, then `rc2`, …), run the workflow on
   that branch, and delete the branch afterwards without merging it. Use a new
   pre-release for every run: the workflow never skips a version that is
   already on TestPyPI, so a repeated version fails instead of testing old
   files. The version check runs only on pull requests, so the branch needs no
   pull request.
3. Publish a GitHub release with tag `vX.Y.Z` matching the version, on that
   commit. Use *Generate release notes* (since the previous `v` tag) and put
   the **Behaviour change:** items from the merged pull requests at the top.
   The workflow builds the distributions and checks their metadata and that
   the tag matches the version. It then installs the wheel and the sdist into
   clean environments, exercises the API and CLI and runs the test suite
   against them, uploads to TestPyPI, installs from there, and only then
   uploads to PyPI.

4. Afterward, open a pull request that moves the **Unreleased** entries in
   `CHANGELOG.md` under a heading for the released version and date, adds that
   version's compare link, and points the `[Unreleased]` link at the new tag
   (`vX.Y.Z...HEAD`), so it lists only what came after the release. (The tag
   must point at a commit that is already on `main`, so the changelog is updated
   after the release rather than before.)

The changelog and the release notes on GitHub are the history of what each release contains. A
version maps to the commit that set it (`git log --oneline -S'__version__ = "0.20.1"' --
cpre/__init__.py`), and every commit on `main` links to its pull request.

One-time setup: the `cpre` project already exists on pypi.org, so add the
trusted publisher under that project's *Publishing* settings: repository
`sahebbiswas/cpre`, workflow `release.yml`, environment `pypi`. On
test.pypi.org, add the same publisher with the environment left empty (any
environment), as for cddl_verifier; use a pending publisher there only if the
TestPyPI project does not exist yet. GitHub creates the `pypi` and `testpypi`
environments on first use; add a required reviewer to `pypi` to gate production
uploads.
