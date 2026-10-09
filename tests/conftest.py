"""Shared pytest configuration.

Set ``CPRE_REQUIRE_INSTALLED=1`` to fail fast if ``cpre`` would be imported
from the source tree instead of an installed wheel or sdist. The release
workflow sets it when it runs the suite against the built distributions.
"""

from __future__ import annotations

import os
from pathlib import Path

if os.environ.get("CPRE_REQUIRE_INSTALLED"):
    import cpre

    _SOURCE = Path(__file__).resolve().parents[1] / "cpre"
    _location = Path(cpre.__file__).resolve().parent
    if _location == _SOURCE:
        raise RuntimeError(
            f"cpre was imported from the source tree ({_location}), "
            "not from an installed distribution"
        )
