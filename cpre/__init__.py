"""Public package interface for cpre."""

from .api import (
    AnalysisResult,
    ConditionError,
    ConditionalTree,
    ContextualSimplification,
    ExactSimplification,
    Finding,
    FindingKind,
    SourceLocation,
    analyze_source,
)

__version__ = "0.3.0"

__all__ = [
    "AnalysisResult",
    "ConditionError",
    "ConditionalTree",
    "ContextualSimplification",
    "ExactSimplification",
    "Finding",
    "FindingKind",
    "SourceLocation",
    "__version__",
    "analyze_source",
]
