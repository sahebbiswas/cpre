"""Public package interface for cpre."""

from .api import (
    AnalysisResult,
    ConditionError,
    ConditionalTree,
    Finding,
    FindingKind,
    SourceLocation,
    analyze_source,
)

__version__ = "0.2.0"

__all__ = [
    "AnalysisResult",
    "ConditionError",
    "ConditionalTree",
    "Finding",
    "FindingKind",
    "SourceLocation",
    "__version__",
    "analyze_source",
]
