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


def add_configuration_options(parser: argparse.ArgumentParser) -> None:
    """The options that build the external macro configuration, shared by ``preprocess`` and ``evaluate``."""
    parser.add_argument(
        "--define",
        "-D",
        dest="defines",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="define an external object-like macro; NAME alone defines an empty macro",
    )
    parser.add_argument(
        "--undef",
        "-U",
        dest="undefined",
        action="append",
        default=[],
        metavar="NAME",
        help="explicitly mark an external macro as undefined",
    )
    parser.add_argument(
        "--config-from",
        dest="config_from",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help=(
            "derive the initial macro configuration from the final macro state of a seed "
            "file such as flags.h; repeatable, applied in order with the last definition "
            "winning; -D/-U are visible while seeds are evaluated and always take precedence"
        ),
    )
    parser.add_argument(
        "--config-exclude",
        dest="config_exclude",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "remove NAME from the configuration derived by --config-from (a private helper "
            "macro or a guard the detector does not recognize); repeatable; the seed still "
            "sees its own definition, and -D/-U of the same name still win"
        ),
    )
    parser.add_argument(
        "--unknown-names",
        choices=[policy.value for policy in UnknownNamePolicy],
        default=UnknownNamePolicy.OPEN.value,
        help=("policy for names absent from the external configuration (default: open)"),
    )
    parser.add_argument(
        "--config-from-unknown-names",
        dest="config_from_unknown_names",
        choices=[policy.value for policy in UnknownNamePolicy],
        default=None,
        help=(
            "policy for names absent from the configuration while --config-from seeds are "
            "evaluated (default: the --unknown-names policy); 'undefined' reads a guarded "
            "flags header with normal C semantics without making the target closed-world"
        ),
    )
    parser.add_argument(
        "--standard-macro",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a deterministic value for a supported standard predefined macro",
    )


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
    exclude: frozenset[str] = frozenset(),
) -> MacroConfiguration | None:
    """Evaluate one ``--config-from`` seed file, reporting failures on stderr.

    ``unknown_names`` is the seed policy; the target policy is restored by
    ``overlay_configuration`` once every seed is applied. ``exclude`` names are removed
    from the layered result, as ``MacroConfiguration.from_source(exclude=)`` does.
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
            exclude=exclude,
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


def _restricted(explicit: MacroConfiguration, names: frozenset[str]) -> MacroConfiguration:
    """The part of ``explicit`` that settles ``names``."""
    return MacroConfiguration(
        definitions=[definition for definition in explicit.definitions if definition.name in names],
        undefined=explicit.undefined & names,
        unknown_names=explicit.unknown_names,
    )


def require_seed_for_seed_options(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject the options that only refine ``--config-from`` when it is not given."""
    if args.config_exclude and not args.config_from:
        parser.error("--config-exclude requires --config-from")
    if args.config_from_unknown_names is not None and not args.config_from:
        parser.error("--config-from-unknown-names requires --config-from")


def _excluded_names(args: argparse.Namespace) -> list[str]:
    """The validated ``--config-exclude`` names."""
    names = []
    for value in args.config_exclude:
        if "=" in value:
            raise ValueError("--config-exclude accepts only a macro name")
        name, _ = parse_name_value(value, "--config-exclude")
        names.append(name)
    return names


def _states(configuration: MacroConfiguration) -> dict[str, tuple[object, ...]]:
    """Each name the configuration settles, mapped to a comparable description of its state."""
    states: dict[str, tuple[object, ...]] = {
        name: ("undefined",) for name in configuration.undefined
    }
    for definition in configuration.definitions:
        states[definition.name] = (
            "defined",
            definition.replacement,
            definition.parameters,
            definition.variadic,
        )
    return states


def configuration_with_origins(
    args: argparse.Namespace,
) -> tuple[MacroConfiguration, PreprocessingContext | None, dict[str, str | None]] | None:
    """The run's configuration plus where each name's final state came from.

    ``origins`` maps a name to the ``--config-from`` seed path that last set it, or to
    ``None`` when ``-D``/``-U`` did. ``None`` is returned after a seed failed.
    """
    configuration, context = _explicit_configuration(args)
    explicit = configuration
    explicit_names = set(_states(explicit))
    origins: dict[str, str | None] = dict.fromkeys(explicit_names)
    if args.config_from:
        seed_policy = UnknownNamePolicy(args.config_from_unknown_names or args.unknown_names)
        excluded = frozenset(_excluded_names(args))
        for seed in args.config_from:
            before = _states(configuration)
            seeded = load_seed_configuration(seed, configuration, context, seed_policy, excluded)
            if seeded is None:
                return None
            after = _states(seeded)
            for name, state in after.items():
                if before.get(name) != state:
                    origins[name] = str(seed)
            for name in before.keys() - after.keys():
                origins.pop(name, None)
            # An exclusion strips a name the command line set explicitly too, but later
            # seeds must still see that setting while their conditions are evaluated.
            configuration = overlay_configuration(seeded, _restricted(explicit, excluded))
            for name in explicit_names & excluded:
                origins[name] = None
        configuration = overlay_configuration(configuration, explicit)
        for name in explicit_names:
            origins[name] = None
    return configuration, context, origins


def configuration_from_arguments(
    args: argparse.Namespace,
) -> tuple[MacroConfiguration, PreprocessingContext | None] | None:
    """The configuration for the run, or ``None`` after a ``--config-from`` seed failed.

    Raises ``ValueError`` for a malformed ``-D``/``-U``/``--standard-macro``/``--config-exclude``
    value.
    """
    built = configuration_with_origins(args)
    return None if built is None else (built[0], built[1])
