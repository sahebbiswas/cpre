"""Reusable source-order macro state; replacement-token expansion is separate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .api import MacroAssumptions, _normalize_assumptions
from .errors import SourceLocation

_NAME = r"[A-Za-z_]\w*"


@dataclass(frozen=True)
class MacroDefinition:
    """A source definition. None parameters means object-like; () means F()."""

    name: str
    replacement: str
    parameters: tuple[str, ...] | None = None
    variadic: bool = False
    location: SourceLocation | None = None

    @property
    def numeric_value(self) -> int | None:
        """Recognize only a (possibly parenthesized/signed) integer literal."""
        if self.parameters is not None:
            return None
        text = self.replacement.strip()
        while text.startswith('(') and text.endswith(')'):
            text = text[1:-1].strip()
        if not re.fullmatch(r"[+-]?\s*(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)[uUlL]*", text):
            return None
        digits = re.sub(r"\s+", "", re.sub(r"[uUlL]+$", "", text))
        sign = -1 if digits.startswith('-') else 1
        digits = digits.lstrip('+-')
        base = 16 if digits.lower().startswith('0x') else (8 if digits.startswith('0') else 10)
        return sign * int(digits, base)


@dataclass(frozen=True)
class MacroState:
    """Independent known definedness/truth; None means unknown, not false."""

    defined: bool | None = None
    value: bool | None = None
    definition: MacroDefinition | None = None


class MacroEnvironment:
    """Mutable per-run state. Snapshots are detached, read-only mappings."""

    def __init__(self, assumptions: MacroAssumptions | Mapping[str, bool] | None = None):
        self._states: dict[str, MacroState] = {}
        normalized = _normalize_assumptions(assumptions)
        if normalized is None:
            return
        values = dict(normalized.values)
        for name in sorted(normalized.defined | normalized.undefined | values.keys()):
            defined = (False if name in normalized.undefined else
                       True if name in normalized.defined or values.get(name) is True else None)
            self._states[name] = MacroState(defined, False if defined is False else values.get(name))

    def get(self, name: str) -> MacroState:
        return self._states.get(name, MacroState())

    def define(self, definition: MacroDefinition) -> None:
        number = definition.numeric_value
        value = False if definition.parameters is not None else (None if number is None else number != 0)
        self._states[definition.name] = MacroState(True, value, definition)

    def undef(self, name: str) -> None:
        self._states[name] = MacroState(False, False)

    def snapshot(self) -> Mapping[str, MacroState]:
        return MappingProxyType(dict(sorted(self._states.items())))


def _apply_macro_directive(environment: MacroEnvironment, kind: str,
                           remainder: str, location: SourceLocation) -> None:
    """Parse a comment-stripped logical directive, or raise ValueError."""
    text = remainder.lstrip()
    match = re.match(_NAME, text)
    if match is None:
        raise ValueError(f'#{kind} requires a macro name')
    name = match.group()
    tail = text[match.end():]
    if kind == 'undef':
        if tail.strip():
            raise ValueError('#undef expects exactly one macro name')
        environment.undef(name)
        return
    parameters = None
    variadic = False
    if tail.startswith('('):
        end = tail.find(')')
        if end == -1:
            raise ValueError('unterminated macro parameter list')
        parts = [part.strip() for part in tail[1:end].split(',')] if tail[1:end].strip() else []
        if parts and parts[-1] == '...':
            variadic = True
            parts.pop()
        if any(not re.fullmatch(_NAME, part) or part == '__VA_ARGS__' for part in parts) or len(set(parts)) != len(parts):
            raise ValueError('unsupported or invalid macro parameter list')
        parameters = tuple(parts)
        tail = tail[end + 1:]
    elif tail and not tail[0].isspace():
        raise ValueError('object-like macro replacement requires whitespace')
    environment.define(MacroDefinition(name, tail.strip(), parameters, variadic, location))


__all__ = ['MacroDefinition', 'MacroState', 'MacroEnvironment']
