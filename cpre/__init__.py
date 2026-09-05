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
    FixConfidence,
    MacroAssumptions,
    ParseError,
    SourceLocation,
    SourceRange,
    SuggestedEdit,
    analyze_source,
)

__version__ = "0.6.0"

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
    "FixConfidence",
    "MacroAssumptions",
    "ParseError",
    "SourceLocation",
    "SourceRange",
    "SuggestedEdit",
    "__version__",
    "analyze_source",
]
