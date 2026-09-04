"""Public package interface for cpre."""

from .api import (
    AnalysisError,
    AnalysisResult,
    ConditionError,
    ConditionalTree,
    ContextualSimplification,
    CpreError,
    ErrorCode,
    ExactSimplification,
    Finding,
    FindingKind,
    ParseError,
    SourceLocation,
    analyze_source,
)

__version__ = "0.4.0"

__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "ConditionError",
    "ConditionalTree",
    "ContextualSimplification",
    "CpreError",
    "ErrorCode",
    "ExactSimplification",
    "Finding",
    "FindingKind",
    "ParseError",
    "SourceLocation",
    "__version__",
    "analyze_source",
]
