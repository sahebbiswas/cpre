"""Stable structured errors for cpre library consumers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SourceLocation:
    """One-based physical source location."""

    line: int
    column: int | None = None


class ErrorCode(str, Enum):
    """Stable machine-readable categories for cpre failures."""

    EXPRESSION_SYNTAX = "expression_syntax"
    MALFORMED_MACRO_DIRECTIVE = "malformed_macro_directive"
    UNMATCHED_DIRECTIVE = "unmatched_directive"
    MISPLACED_DIRECTIVE = "misplaced_directive"
    TRAILING_DIRECTIVE_TEXT = "trailing_directive_text"
    UNTERMINATED_CONDITIONAL = "unterminated_conditional"
    INVALID_ASSUMPTIONS = "invalid_assumptions"
    ANALYSIS_FAILURE = "analysis_failure"


class CpreError(ValueError):
    """Base class for supported cpre library failures.

    ``message`` is human-readable text. Consumers should use ``code`` and
    ``location`` for machine-readable handling rather than parsing ``str(exc)``.
    ``filename`` is optional source identity metadata and is intentionally kept
    separate from the rendered message.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        location: SourceLocation | None = None,
        filename: str | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.location = location
        self.filename = filename
        super().__init__(message)


class ParseError(CpreError):
    """Raised when conditional directives or expressions are malformed."""


class AnalysisError(CpreError):
    """Raised for supported failures after parsing has completed."""


__all__ = [
    "AnalysisError",
    "CpreError",
    "ErrorCode",
    "ParseError",
    "SourceLocation",
]
