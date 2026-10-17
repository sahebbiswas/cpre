"""Replacement lists: the first stage of macro expansion.

A macro's replacement text is tokenized once, and its ``__VA_OPT__(...)`` groups are
lifted out of the body into numbered bindings. This module also holds the errors and
operator predicates that every later stage shares, so it sits at the bottom of the
expansion stages (``_arguments``, ``_substitution``, ``_rescan``, ``expansion``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import Token, tokenize
from .macros import MacroDefinition

VA_OPT_KIND = "va_opt"


class ExpansionError(ValueError):
    """An unsupported or malformed invocation encountered during expansion."""


def is_paste_operator(token: Token) -> bool:
    return token.kind == "other" and token.text in {"##", "%:%:"}


def is_stringify_operator(token: Token) -> bool:
    return token.kind == "other" and token.text in {"#", "%:"}


@dataclass(frozen=True)
class Replacement:
    """A tokenized replacement list.

    ``body`` has every ``__VA_OPT__(...)`` group replaced by one placeholder token of
    kind ``va_opt``; ``va_opts`` maps that token's text to the group's content.
    """

    body: list[Token]
    va_opts: dict[str, list[Token]]


def validate_va_opt_content(content: list[Token], definition: MacroDefinition) -> None:
    """Validate #/## placement inside a va-opt replacement list."""
    if not content:
        return
    if is_paste_operator(content[0]) or is_paste_operator(content[-1]):
        raise ExpansionError(
            f"token paste operator cannot appear at an edge of __VA_OPT__ in {definition.name}"
        )
    parameters = set(definition.parameters or ()) | {"__VA_ARGS__"}
    for index, part in enumerate(content):
        if is_paste_operator(part):
            if index + 1 >= len(content) or is_paste_operator(content[index + 1]):
                raise ExpansionError(
                    f"invalid token paste placement in __VA_OPT__ of {definition.name}"
                )
            continue
        if is_stringify_operator(part):
            if index + 1 >= len(content):
                raise ExpansionError(
                    f"stringification operator in __VA_OPT__ of {definition.name} "
                    "is not followed by a parameter"
                )
            operand = content[index + 1]
            if operand.kind != "identifier" or operand.text not in parameters:
                raise ExpansionError(
                    f"stringification operator in __VA_OPT__ of {definition.name} "
                    "is not followed by a parameter"
                )


def _read_va_opt(
    tokens: list[Token], index: int, definition: MacroDefinition
) -> tuple[list[Token], int]:
    """The content of the ``__VA_OPT__`` at ``tokens[index]`` and the index of its ``)``."""
    if definition.parameters is None or not definition.variadic:
        raise ExpansionError(
            f"__VA_OPT__ requires a variadic function-like macro: {definition.name}"
        )
    if index + 1 >= len(tokens) or tokens[index + 1].text != "(":
        raise ExpansionError(
            f"__VA_OPT__ in {definition.name} must be followed by a parenthesized token sequence"
        )

    depth = 0
    cursor = index + 2
    content: list[Token] = []
    while cursor < len(tokens):
        current = tokens[cursor]
        if current.kind == "identifier" and current.text == "__VA_OPT__":
            raise ExpansionError(f"nested __VA_OPT__ is not allowed in {definition.name}")
        if current.text == "(":
            depth += 1
        elif current.text == ")":
            if depth == 0:
                break
            depth -= 1
        content.append(current)
        cursor += 1
    if cursor >= len(tokens):
        raise ExpansionError(f"unterminated __VA_OPT__ in {definition.name}")
    validate_va_opt_content(content, definition)
    return content, cursor


def parse_replacement(definition: MacroDefinition) -> Replacement:
    """Tokenize a replacement list and lift its ``__VA_OPT__`` groups into bindings."""
    tokens = [t for t in tokenize(definition.replacement) if t.kind not in {"space", "comment"}]
    body: list[Token] = []
    va_opts: dict[str, list[Token]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "identifier" or token.text != "__VA_OPT__":
            body.append(token)
            index += 1
            continue
        content, cursor = _read_va_opt(tokens, index, definition)
        binding = f"\x00va_opt_{len(va_opts)}"
        body.append(
            Token(
                binding,
                VA_OPT_KIND,
                token.start,
                tokens[cursor].end,
                space_before=token.space_before,
            )
        )
        va_opts[binding] = content
        index = cursor + 1
    return Replacement(body, va_opts)


class ReplacementCache:
    """Parsed replacement lists, shared by every invocation of the same definition."""

    def __init__(self) -> None:
        self._parsed: dict[tuple[str, tuple[str, ...] | None, bool], Replacement] = {}

    def get(self, definition: MacroDefinition) -> Replacement:
        key = (definition.replacement, definition.parameters, definition.variadic)
        parsed = self._parsed.get(key)
        if parsed is None:
            parsed = self._parsed[key] = parse_replacement(definition)
        return parsed

    def validate(self, definition: MacroDefinition) -> None:
        """Validate replacement-list constructs whose legality is definition-local."""
        if definition.name == "__VA_OPT__":
            raise ExpansionError("__VA_OPT__ cannot be used as a macro name")
        if definition.parameters is not None and "__VA_OPT__" in definition.parameters:
            raise ExpansionError(f"__VA_OPT__ cannot be a named parameter: {definition.name}")
        self.get(definition)
