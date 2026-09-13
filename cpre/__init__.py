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
from .pragmas import (
    Pragma,
    PragmaDisposition,
    PragmaHandler,
    PragmaOrigin,
    preprocess_source,
)

__version__ = "0.10.16"

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
    "MacroConfiguration",
    "MacroDefinition",
    "MacroEnvironment",
    "MacroState",
    "ParseError",
    "Pragma",
    "PragmaDisposition",
    "PragmaHandler",
    "PragmaOrigin",
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
