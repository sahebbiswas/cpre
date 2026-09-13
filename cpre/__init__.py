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

from .configuration import MacroConfiguration, UnknownNamePolicy
from .expansion import SourceMapping
from .macros import MacroDefinition, MacroEnvironment, MacroState
from .preprocessing import (
    PreprocessingContext,
    PreprocessDiagnostic,
    PreprocessResult,
    compact,
)
from .include_queries import IncludeForm, IncludeQuery, IncludeQueryProvider, preprocess_source

__version__ = "0.10.15"

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
    "IncludeForm",
    "IncludeQuery",
    "IncludeQueryProvider",
    "MacroAssumptions",
    "MacroConfiguration",
    "MacroDefinition",
    "MacroEnvironment",
    "MacroState",
    "ParseError",
    "PreprocessingContext",
    "PreprocessDiagnostic",
    "PreprocessResult",
    "UnknownNamePolicy",
    "compact",
    "preprocess_source",
    "SourceLocation",
    "SourceMapping",
    "SourceRange",
    "SuggestedEdit",
    "__version__",
    "analyze_source",
]
