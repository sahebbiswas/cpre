"""Host-assisted support for the standard ``__has_include`` operator.

``preprocess_source`` here is :func:`cpre.preprocessing.preprocess_source`: that function
already takes ``include_query=`` and ``has_include_from_resolver=``, so this module only
keeps the documented import path.
"""

from __future__ import annotations

from ._has_include import IncludeQuery, IncludeQueryProvider
from .includes import IncludeForm
from .preprocessing import preprocess_source

__all__ = [
    "IncludeForm",
    "IncludeQuery",
    "IncludeQueryProvider",
    "preprocess_source",
]
