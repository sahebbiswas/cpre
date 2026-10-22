"""Shared pytest configuration.

Set ``CPRE_REQUIRE_INSTALLED=1`` to fail fast if ``cpre`` would be imported
from the source tree instead of an installed wheel or sdist. The release
workflow sets it when it runs the suite against the built distributions.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("CPRE_REQUIRE_INSTALLED"):
    import cpre

    _SOURCE = Path(__file__).resolve().parents[1] / "cpre"
    _location = Path(cpre.__file__).resolve().parent
    if _location == _SOURCE:
        raise RuntimeError(
            f"cpre was imported from the source tree ({_location}), "
            "not from an installed distribution"
        )


@pytest.fixture(autouse=True)
def _terminal_independent_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep exact-output tests independent of the terminal and CI environment.

    ``--color auto`` and ``--progress auto`` read NO_COLOR, FORCE_COLOR, TERM and COLUMNS, and a
    Windows console must have ANSI processing switched on; none of that may change what a
    test sees. Tests of those rules set the variables (or patch the check) themselves.
    """
    for name in ("NO_COLOR", "FORCE_COLOR", "COLUMNS", "TERM"):
        monkeypatch.delenv(name, raising=False)
    from cpre import _style

    monkeypatch.setattr(_style, "_enable_windows_vt", lambda stream: True)
