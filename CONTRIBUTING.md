# Contributing to `cpre`

Thank you for contributing! Here are some instructions to get your local development environment set up and ensure your code is ready to be merged.

## Prerequisites

- **Python 3.9+**
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

## Code Style

- **Formatting & Linting**: We use `ruff`.
- **Typing**: We use `mypy`. All new code should be fully typed and pass `mypy` with `--strict`.

When opening a Pull Request, our CI will automatically verify these checks.
