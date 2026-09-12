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

from .expansion import SourceMapping
from .macros import MacroDefinition, MacroEnvironment, MacroState
from .preprocessing import PreprocessDiagnostic, PreprocessResult, preprocess_source

__version__ = "0.10.5"

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
    "MacroDefinition",
    "MacroEnvironment",
    "MacroState",
    "ParseError",
    "PreprocessDiagnostic",
    "PreprocessResult",
    "preprocess_source",
    "SourceLocation",
    "SourceMapping",
    "SourceRange",
    "SuggestedEdit",
    "__version__",
    "analyze_source",
]
