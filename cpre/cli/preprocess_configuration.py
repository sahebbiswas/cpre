"""``cpre preprocess``: build the external macro configuration from command-line options."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._types import AnalysisIncomplete
from ..configuration import MacroConfiguration, UnknownNamePolicy
from ..errors import CpreError, ErrorCode, IncompleteConfigurationError
from ..macros import MacroDefinition
from ..preprocessing import PreprocessDiagnostic, PreprocessingContext
from ..text_reports import format_error, format_incomplete


def parse_name_value(value: str, option: str) -> tuple[str, str]:
    """Parse a CLI macro assignment into a name and replacement text."""
    if "=" in value:
        name, replacement = value.split("=", 1)
    else:
        name, replacement = value, ""
    if (
        not name
        or not (name[0].isalpha() or name[0] == "_")
        or not all(ch.isalnum() or ch == "_" for ch in name)
    ):
        raise ValueError(f"{option}: invalid macro name {name!r}")
    return name, replacement


def overlay_configuration(
    seeded: MacroConfiguration, explicit: MacroConfiguration
) -> MacroConfiguration:
    """Re-apply explicit command-line macros on top of a seed-derived configuration."""
    explicit_names = {definition.name for definition in explicit.definitions} | explicit.undefined
    return MacroConfiguration(
        definitions=[
            *(
                definition
                for definition in seeded.definitions
                if definition.name not in explicit_names
            ),
            *explicit.definitions,
        ],
        undefined=(seeded.undefined - explicit_names) | explicit.undefined,
        unknown_names=explicit.unknown_names,
    )


def _report_seed_failure(
    seed: Path, error: IncompleteConfigurationError, unknown_names: UnknownNamePolicy
) -> None:
    diagnostics = [
        diagnostic
        for diagnostic in error.incomplete
        if isinstance(diagnostic, PreprocessDiagnostic)
    ]
    if not diagnostics:
        print(f"{seed}: invalid --config-from seed: {format_error(error)}", file=sys.stderr)
    for diagnostic in diagnostics:
        origin = diagnostic.source_identity or seed
        print(
            f"{origin}: invalid --config-from seed: {format_incomplete(diagnostic)}",
            file=sys.stderr,
        )
    if unknown_names is UnknownNamePolicy.OPEN and any(
        diagnostic.code is ErrorCode.UNRESOLVED_CONDITION for diagnostic in diagnostics
    ):
        print(
            f"{seed}: hint: seed conditions such as include guards on undefined names "
            "need --config-from-unknown-names undefined (seed only) or "
            "--unknown-names undefined (seed and target)",
            file=sys.stderr,
        )


def load_seed_configuration(
    seed: Path,
    base: MacroConfiguration,
    context: PreprocessingContext | None,
    unknown_names: UnknownNamePolicy,
) -> MacroConfiguration | None:
    """Evaluate one ``--config-from`` seed file, reporting failures on stderr.

    ``unknown_names`` is the seed policy; the target policy is restored by
    ``overlay_configuration`` once every seed is applied.
    """
    if seed.is_dir():
        print(f"{seed}: --config-from requires a file, not a directory", file=sys.stderr)
        return None
    try:
        with seed.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        return MacroConfiguration.from_source(
            text,
            filename=str(seed),
            context=context,
            unknown_names=unknown_names,
            base=base,
        )
    except IncompleteConfigurationError as error:
        _report_seed_failure(seed, error, unknown_names)
    except CpreError as error:
        print(f"{seed}: invalid --config-from seed: {format_error(error)}", file=sys.stderr)
    except OSError as error:
        print(f"{seed}: cannot read --config-from seed: {error.strerror or error}", file=sys.stderr)
    except UnicodeDecodeError as error:
        print(f"{seed}: cannot read --config-from seed: {error}", file=sys.stderr)
    return None


def _explicit_configuration(
    args: argparse.Namespace,
) -> tuple[MacroConfiguration, PreprocessingContext | None]:
    """The configuration named by ``-D``, ``-U``, ``--standard-macro`` and ``--unknown-names``."""
    definitions: list[MacroDefinition] = []
    presence: list[str] = []
    for value in args.defines:
        name, replacement = parse_name_value(value, "--define")
        if replacement:
            definitions.append(MacroDefinition(name, replacement))
        else:
            presence.append(name)

    undefined = []
    for value in args.undefined:
        if "=" in value:
            raise ValueError("--undef accepts only a macro name")
        name, _ = parse_name_value(value, "--undef")
        undefined.append(name)

    standard_macros: dict[str, str] = {}
    for value in args.standard_macro:
        name, replacement = parse_name_value(value, "--standard-macro")
        if not replacement:
            raise ValueError("--standard-macro requires NAME=VALUE")
        standard_macros[name] = replacement

    configuration = MacroConfiguration(
        presence=presence,
        undefined=undefined,
        definitions=definitions,
        unknown_names=args.unknown_names,
    )
    context = PreprocessingContext(standard_macros=standard_macros) if standard_macros else None
    return configuration, context


def configuration_from_arguments(
    args: argparse.Namespace,
) -> tuple[MacroConfiguration, PreprocessingContext | None] | None:
    """The configuration for the run, or ``None`` after a ``--config-from`` seed failed.

    Raises ``ValueError`` for a malformed ``-D``/``-U``/``--standard-macro`` value.
    """
    configuration, context = _explicit_configuration(args)
    if args.config_from:
        explicit = configuration
        seed_policy = UnknownNamePolicy(args.config_from_unknown_names or args.unknown_names)
        for seed in args.config_from:
            seeded = load_seed_configuration(seed, configuration, context, seed_policy)
            if seeded is None:
                return None
            configuration = seeded
        configuration = overlay_configuration(configuration, explicit)
    return configuration, context
