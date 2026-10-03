"""Concrete caller-supplied macro configuration for preprocessing runs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import AnalysisOptions
    from .preprocessing import PreprocessingContext

from .errors import AnalysisError, ErrorCode
from .macros import MacroDefinition, MacroEnvironment, MacroState

_NAME = re.compile(r"[A-Za-z_]\w*\Z")


class UnknownNamePolicy(str, Enum):
    """How concrete preprocessing treats macros absent from configuration/state."""

    OPEN = "open"
    UNDEFINED = "undefined"


@dataclass(frozen=True, init=False)
class MacroConfiguration:
    """Concrete macro definitions supplied before source preprocessing.

    ``presence`` creates empty object-like definitions, ``integers`` creates
    object-like integer definitions, and ``definitions`` accepts complete
    :class:`MacroDefinition` objects for arbitrary replacement text and
    function-like macros. ``undefined`` records names that are explicitly absent.

    ``unknown_names=UnknownNamePolicy.UNDEFINED`` opts into C-preprocessor/pcpp
    closed-world condition semantics: names not otherwise known are treated as
    undefined, and remaining identifiers in ``#if`` expressions evaluate as zero.
    The default remains open-world so unmentioned names stay unknown.
    """

    definitions: tuple[MacroDefinition, ...]
    undefined: frozenset[str]
    unknown_names: UnknownNamePolicy

    def __init__(
        self,
        *,
        presence: Iterable[str] = (),
        undefined: Iterable[str] = (),
        integers: Mapping[str, int] | None = None,
        definitions: Iterable[MacroDefinition] = (),
        unknown_names: UnknownNamePolicy | str = UnknownNamePolicy.OPEN,
    ) -> None:
        present = frozenset(presence)
        absent = frozenset(undefined)
        integer_values = dict(integers or {})
        supplied = tuple(definitions)

        try:
            policy = UnknownNamePolicy(unknown_names)
        except (TypeError, ValueError) as error:
            raise AnalysisError(
                f"unknown macro-name policy: {unknown_names!r}",
                code=ErrorCode.INVALID_CONFIGURATION,
            ) from error

        names = set(present) | set(absent) | set(integer_values)
        for name in names:
            self._validate_name(name)
        for name, value in integer_values.items():
            if type(value) is not int:
                raise AnalysisError(
                    f"integer macro value for {name} must be an int",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )

        normalized: list[MacroDefinition] = []
        normalized.extend(MacroDefinition(name, "") for name in sorted(present))
        normalized.extend(
            MacroDefinition(name, str(value)) for name, value in sorted(integer_values.items())
        )

        supplied_names: set[str] = set()
        for definition in supplied:
            if not isinstance(definition, MacroDefinition):
                raise AnalysisError(
                    "definitions must contain MacroDefinition instances",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            self._validate_definition(definition)
            if definition.name in supplied_names:
                raise AnalysisError(
                    f"duplicate configured macro definition: {definition.name}",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            supplied_names.add(definition.name)
            # External definitions have no physical source definition location.
            normalized.append(replace(definition, location=None))

        defined_names = present | set(integer_values) | supplied_names
        collisions = sorted(
            (present & set(integer_values))
            | (present & supplied_names)
            | (set(integer_values) & supplied_names)
            | (defined_names & absent)
        )
        if collisions:
            raise AnalysisError(
                f"macro has conflicting concrete configuration: {collisions[0]}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )

        object.__setattr__(
            self, "definitions", tuple(sorted(normalized, key=lambda item: item.name))
        )
        object.__setattr__(self, "undefined", absent)
        object.__setattr__(self, "unknown_names", policy)

    @classmethod
    def from_source(
        cls,
        text: str,
        *,
        filename: str | None = None,
        context: PreprocessingContext | None = None,
        unknown_names: UnknownNamePolicy | str = UnknownNamePolicy.OPEN,
        options: AnalysisOptions | None = None,
        base: MacroConfiguration | None = None,
    ) -> MacroConfiguration:
        """Derive a configuration from the final macro state of a seed source.

        When ``base`` is supplied, the seed is evaluated on top of the base's
        definitions and undefined names, and the last definition wins: names
        the seed never touches keep their base state, a seed ``#define``
        replaces whatever category the base used, and a seed ``#undef`` leaves
        the name only undefined. Evaluation and the returned configuration
        always use the explicit ``unknown_names`` argument, never
        ``base.unknown_names``.
        """
        if type(text) is not str:
            raise AnalysisError(
                "text must be a string",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
        if base is not None and not isinstance(base, MacroConfiguration):
            raise AnalysisError(
                "base must be a MacroConfiguration instance or None",
                code=ErrorCode.INVALID_CONFIGURATION,
            )

        from .errors import IncompleteConfigurationError
        from .pragmas import preprocess_source

        if base is None:
            seed_config = cls(unknown_names=unknown_names)
        else:
            # Pass base definitions through ``definitions=`` so presence and
            # integer replacement text is preserved exactly; the policy comes
            # from the explicit argument so base's policy cannot leak.
            seed_config = cls(
                definitions=base.definitions,
                undefined=base.undefined,
                unknown_names=unknown_names,
            )
        result = preprocess_source(
            text,
            filename=filename,
            configuration=seed_config,
            context=context,
            options=options,
        )

        if not result.complete:
            first = result.incomplete[0]
            raise IncompleteConfigurationError(
                getattr(first, "message", "incomplete configuration"),
                code=getattr(first, "code", ErrorCode.ANALYSIS_FAILURE),
                location=getattr(first, "location", None),
                filename=filename,
                incomplete=result.incomplete,
            )

        assert result.macros is not None
        presence = []
        undefined = []
        integers = {}
        definitions = []

        context_names = set(context.standard_macros.keys()) if context is not None else set()

        for name, state in result.macros.items():
            if (
                name in context_names
                and state.defined
                and state.definition is not None
                and state.definition.location is None
            ):
                continue

            if state.defined is False and state.definition is None:
                undefined.append(name)
            elif state.defined is True and state.definition is not None:
                rep = state.definition.replacement
                if state.definition.parameters is None:
                    if rep == "":
                        presence.append(name)
                        continue
                    try:
                        val = int(rep)
                        if str(val) == rep and val != 0:
                            integers[name] = val
                            continue
                        elif val == 0 and rep == "0":
                            integers[name] = val
                            continue
                    except ValueError:
                        pass
                definitions.append(state.definition)
            else:
                raise AnalysisError(
                    "invalid macro state snapshot",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )

        return cls(
            presence=presence,
            undefined=undefined,
            integers=integers,
            definitions=definitions,
            unknown_names=unknown_names,
        )

    @staticmethod
    def _validate_name(name: object) -> None:
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise AnalysisError(
                f"invalid macro name in configuration: {name!r}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )

    @classmethod
    def _validate_definition(cls, definition: MacroDefinition) -> None:
        cls._validate_name(definition.name)
        if definition.parameters is None:
            if definition.variadic:
                raise AnalysisError(
                    f"object-like macro cannot be variadic: {definition.name}",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            return
        if len(set(definition.parameters)) != len(definition.parameters):
            raise AnalysisError(
                f"duplicate parameter in configured macro: {definition.name}",
                code=ErrorCode.INVALID_CONFIGURATION,
            )
        for parameter in definition.parameters:
            if parameter == "__VA_ARGS__":
                raise AnalysisError(
                    f"__VA_ARGS__ cannot be a named parameter: {definition.name}",
                    code=ErrorCode.INVALID_CONFIGURATION,
                )
            cls._validate_name(parameter)


class _ConfiguredMacroEnvironment(MacroEnvironment):
    """MacroEnvironment seeded from a concrete external configuration."""

    def __init__(self, configuration: MacroConfiguration) -> None:
        super().__init__()
        self._unknown_names = configuration.unknown_names
        for name in sorted(configuration.undefined):
            self.undef(name)
        for definition in configuration.definitions:
            self.define(definition)

    def get(self, name: str) -> MacroState:
        state = super().get(name)
        if (
            self._unknown_names is UnknownNamePolicy.UNDEFINED
            and state.defined is None
            and state.value is None
            and state.definition is None
        ):
            return MacroState(False, False)
        return state


class _ConditionMacroEnvironment:
    """Condition-only view that turns known-undefined identifiers into literal 0."""

    def __init__(self, environment: MacroEnvironment) -> None:
        self._environment = environment

    def get(self, name: str) -> MacroState:
        state = self._environment.get(name)
        if state.defined is False and state.definition is None:
            return MacroState(False, False, MacroDefinition(name, "0"))
        return state


def _configured_environment(configuration: MacroConfiguration) -> MacroEnvironment:
    if not isinstance(configuration, MacroConfiguration):
        raise AnalysisError(
            "configuration must be a MacroConfiguration instance",
            code=ErrorCode.INVALID_CONFIGURATION,
        )
    return _ConfiguredMacroEnvironment(configuration)


def _condition_environment(environment: MacroEnvironment) -> MacroEnvironment:
    # The wrapper intentionally implements the MacroEnvironment read contract
    # without mutating the caller-visible final snapshot.
    return _ConditionMacroEnvironment(environment)  # type: ignore[return-value]


__all__ = ["MacroConfiguration", "UnknownNamePolicy"]
