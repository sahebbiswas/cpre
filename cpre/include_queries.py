"""Host-assisted support for the standard ``__has_include`` operator."""

from __future__ import annotations

from collections.abc import Mapping

from ._has_include import IncludeQuery, IncludeQueryProvider
from .api import AnalysisOptions, MacroAssumptions
from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode
from .includes import DEFAULT_MAX_INCLUDE_DEPTH, IncludeForm, IncludeResolver
from .preprocessing import PreprocessingContext, PreprocessResult
from .preprocessing import preprocess_source as _core_preprocess_source


def preprocess_source(
    source: str,
    *,
    filename: str | None = None,
    assumptions: MacroAssumptions | Mapping[str, bool] | None = None,
    configuration: MacroConfiguration | None = None,
    context: PreprocessingContext | None = None,
    include_query: IncludeQueryProvider | None = None,
    options: AnalysisOptions | None = None,
    skip_includes: bool = False,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
    has_include_from_resolver: bool = False,
    _dispatch_included_pragmas: bool = False,
) -> PreprocessResult:
    """Concrete preprocessing with optional host-assisted ``__has_include`` support.

    cpre never searches the filesystem or emulates compiler include paths. When a
    reachable condition requires ``__has_include``, ``include_query`` receives the
    macro-expanded header spelling, delimiter form, physical source location, and
    the identity of the source containing the query: ``filename`` for the primary
    source, or the resolved identity of an included source. A Boolean answer is
    substituted deterministically; ``None`` yields an atomic incomplete result.
    Queries in branches or Boolean terms proven unreachable are never sent to the
    provider. With ``has_include_from_resolver=True``, a query that
    ``include_query`` does not answer (no callback, or ``None``) is answered by
    ``include_resolver``: the header is available exactly when the resolver returns
    a :class:`ResolvedInclude` for the equivalent ``#include`` request. The resolver
    must have no side effects for this to be sound, so it is opt-in.
    ``defined(__has_include)`` is true when either source of answers is enabled;
    ``__has_include_next`` is unsupported.
    """
    if include_query is not None and not callable(include_query):
        raise AnalysisError(
            "include_query must be callable",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    return _core_preprocess_source(
        source,
        filename=filename,
        assumptions=assumptions,
        configuration=configuration,
        context=context,
        options=options,
        skip_includes=skip_includes,
        include_resolver=include_resolver,
        max_include_depth=max_include_depth,
        _dispatch_included_pragmas=_dispatch_included_pragmas,
        _include_query=include_query,
        _has_include_from_resolver=has_include_from_resolver,
    )


# Importing the package initializes this module after ``cpre.preprocessing``.
# Keep the documented submodule path compatible with the host-assisted wrapper.
from . import preprocessing as _preprocessing_module

# The wrapper takes ``include_query`` in place of the core's private ``_include_query``.
_preprocessing_module.preprocess_source = preprocess_source  # type: ignore[assignment]


__all__ = [
    "IncludeForm",
    "IncludeQuery",
    "IncludeQueryProvider",
    "preprocess_source",
]
