"""Public package interface for cpre."""

from .api import (
    AnalysisError,
    AnalysisIncomplete,
    AnalysisOptions,
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

from .preprocessing import PreprocessDiagnostic, PreprocessResult, preprocess_source

__version__ = "0.8.0"

__all__ = [
    "AnalysisError",
    "AnalysisIncomplete",
    "AnalysisOptions",
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
    "PreprocessDiagnostic",
    "PreprocessResult",
    "preprocess_source",
    "SourceLocation",
    "SourceRange",
    "SuggestedEdit",
    "__version__",
    "analyze_source",
]
