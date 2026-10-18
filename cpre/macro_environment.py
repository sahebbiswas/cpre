"""The macro environment a concrete preprocessing run starts from.

This is cpre's *configured* environment: the macros supplied by the command line,
``--config-from`` seed files and the standard-macro context. It is not a compiler's
predefined-macro catalog, and it does not include macros the preprocessed source
defines itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .configuration import MacroConfiguration
from .errors import AnalysisError, ErrorCode
from .preprocessing import PreprocessingContext

COMMAND_LINE = "command-line"
SEED = "seed"
STANDARD_MACRO = "standard-macro"


@dataclass(frozen=True)
class MacroEnvironmentEntry:
    """One name of the configured environment and where its state came from."""

    name: str
    defined: bool
    replacement: str | None
    parameters: tuple[str, ...] | None
    variadic: bool
    origin: str
    path: str | None


def macro_environment(
    configuration: MacroConfiguration,
    context: PreprocessingContext | None = None,
    origins: Mapping[str, str | None] | None = None,
) -> list[MacroEnvironmentEntry]:
    """The configured entries sorted by name, then origin.

    Raises ``AnalysisError`` when ``context`` supplies a name the configuration also
    settles, exactly as preprocessing does.

    ``origins`` maps a name to the seed path that set it; a name mapped to ``None`` (or
    absent) came from the command line, or from the caller when no CLI is involved.
    """
    origins = origins or {}

    def origin_of(name: str) -> tuple[str, str | None]:
        path = origins.get(name)
        return (SEED, path) if path is not None else (COMMAND_LINE, None)

    entries: dict[tuple[str, str], MacroEnvironmentEntry] = {}
    for name in configuration.undefined:
        kind, path = origin_of(name)
        entries[(name, kind)] = MacroEnvironmentEntry(name, False, None, None, False, kind, path)
    for definition in configuration.definitions:
        kind, path = origin_of(definition.name)
        entries[(definition.name, kind)] = MacroEnvironmentEntry(
            definition.name,
            True,
            definition.replacement,
            definition.parameters,
            definition.variadic,
            kind,
            path,
        )
    if context is not None:
        configured = {name for name, _ in entries}
        for name, replacement in context.standard_macros.items():
            if name in configured:
                # The same conflict preprocessing itself reports.
                raise AnalysisError(
                    f"preprocessing context conflicts with configured macro: {name}",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            entries[(name, STANDARD_MACRO)] = MacroEnvironmentEntry(
                name, True, replacement, None, False, STANDARD_MACRO, None
            )
    return [entries[key] for key in sorted(entries)]
