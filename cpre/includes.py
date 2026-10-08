"""Caller-owned include resolution for concrete preprocessing.

cpre owns recursive preprocessing of resolved includes: macro-state propagation,
cycle and depth checks, ``#pragma once`` and include-guard handling, provenance,
and diagnostics. Mapping an include request to source text is delegated to an
:class:`IncludeResolver` supplied by the caller, so the library never embeds a
compiler-specific header search policy. :class:`SearchPathResolver` is an optional
filesystem resolver used by the CLI.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Union

from .errors import AnalysisError, ErrorCode, SourceLocation

DEFAULT_MAX_INCLUDE_DEPTH = 200


class IncludeForm(str, Enum):
    """Syntactic header-name form used by an include directive or ``__has_include``."""

    QUOTED = "quoted"
    ANGLE = "angle"


@dataclass(frozen=True)
class IncludeRequest:
    """One reachable include directive that needs a source.

    ``header`` is the spelling between delimiters; computed includes are macro
    expanded first. ``directive`` is ``"include"``, ``"include_next"``, or
    ``"import"``. ``includer`` is the identity of the source containing the
    directive: the caller-supplied ``filename`` for the primary source, otherwise
    the :attr:`ResolvedInclude.identity` of an included source. ``depth`` is the
    nesting depth of the includer (0 for the primary source). ``location`` is the
    directive's physical location within the includer.
    """

    header: str
    form: IncludeForm
    directive: str
    location: SourceLocation
    includer: str | None = None
    depth: int = 0


@dataclass(frozen=True)
class ResolvedInclude:
    """Source text for a resolved include and its deterministic identity.

    ``identity`` must be stable for the same underlying source: cpre uses it for
    cycle detection, ``#pragma once``, include guards, ``__FILE__``, diagnostics,
    and :attr:`SourceMapping.source_identity` provenance.
    """

    identity: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity:
            raise AnalysisError(
                "ResolvedInclude.identity must be a non-empty string",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
        if not isinstance(self.source, str):
            raise AnalysisError(
                "ResolvedInclude.source must be a string",
                code=ErrorCode.INVALID_CONFIGURATION,
            )


class IncludeResolver(Protocol):
    """Callable host policy that maps an include request to source text."""

    def __call__(self, request: IncludeRequest) -> ResolvedInclude | None:
        """Return the included source, or ``None`` when it cannot be resolved."""
        ...


class IncludeOutcome(str, Enum):
    """What concrete preprocessing did with a resolved include."""

    ENTERED = "entered"
    PRAGMA_ONCE = "pragma_once"
    IMPORTED = "imported"
    GUARDED = "guarded"


@dataclass(frozen=True)
class IncludeRecord:
    """Provenance for one reachable include directive that the resolver resolved.

    ``ENTERED`` sources were preprocessed and spliced into the output.
    ``PRAGMA_ONCE``, ``IMPORTED`` (the source was already entered and either
    this directive or an earlier one was ``#import``), and ``GUARDED`` (the
    source's include guard macro was already defined) were not re-entered and
    contribute no output.
    """

    request: IncludeRequest
    identity: str
    outcome: IncludeOutcome


PathLike = Union[str, "os.PathLike[str]"]


class SearchPathResolver:
    """Resolve includes from the filesystem using explicit search directories.

    Quoted includes search the includer's directory, then ``quote_paths``, then
    ``search_paths``. Angle-bracket includes search only ``search_paths``.
    ``#include_next`` continues the search after the directory where the includer
    was found. Identities are normalized joined paths, so they are deterministic
    for the same command line. No compiler default directories are consulted.
    """

    def __init__(
        self,
        search_paths: Sequence[PathLike] = (),
        *,
        quote_paths: Sequence[PathLike] = (),
        encoding: str = "utf-8",
    ) -> None:
        self.search_paths = tuple(os.fspath(path) for path in search_paths)
        self.quote_paths = tuple(os.fspath(path) for path in quote_paths)
        self.encoding = encoding

    def _chain(self, form: IncludeForm) -> tuple[str, ...]:
        if form is IncludeForm.QUOTED:
            return self.quote_paths + self.search_paths
        return self.search_paths

    def _directories(self, request: IncludeRequest) -> tuple[str, ...]:
        chain = self._chain(request.form)
        includer_dir = (
            os.path.dirname(request.includer) or os.curdir if request.includer is not None else None
        )
        if request.directive == "include_next":
            if includer_dir is not None:
                normalized = os.path.normpath(includer_dir)
                for index, directory in enumerate(chain):
                    if os.path.normpath(directory) == normalized:
                        return chain[index + 1 :]
            return chain
        if request.form is IncludeForm.QUOTED and includer_dir is not None:
            return (includer_dir, *chain)
        return chain

    def __call__(self, request: IncludeRequest) -> ResolvedInclude | None:
        if os.path.isabs(request.header):
            candidates = [request.header]
        else:
            candidates = [
                os.path.join(directory, request.header) for directory in self._directories(request)
            ]
        for candidate in candidates:
            if not os.path.isfile(candidate):
                continue
            identity = os.path.normpath(candidate)
            try:
                with open(candidate, encoding=self.encoding, newline="") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError) as error:
                raise AnalysisError(
                    f"cannot read included source {identity}: {error}",
                    code=ErrorCode.SOURCE_READ_ERROR,
                    location=request.location,
                    filename=request.includer,
                ) from error
            return ResolvedInclude(identity, text)
        return None


__all__ = [
    "DEFAULT_MAX_INCLUDE_DEPTH",
    "IncludeForm",
    "IncludeOutcome",
    "IncludeRecord",
    "IncludeRequest",
    "IncludeResolver",
    "ResolvedInclude",
    "SearchPathResolver",
]
