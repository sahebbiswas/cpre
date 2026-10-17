"""Substitution: the third stage of macro expansion.

Applies ``#``, ``##``, ``__VA_OPT__`` and parameter replacement to a replacement list,
producing the tokens that are then rescanned. It takes the raw (invocation-spelling)
and prescanned arguments as inputs and never looks at the macro environment.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, Union

from ._replacement import (
    VA_OPT_KIND,
    ExpansionError,
    is_paste_operator,
    is_stringify_operator,
)
from .lexer import Token, tokenize
from .macros import MacroDefinition

_PASTE: Literal["##"] = "##"
Element = Union[list[Token], Literal["##"]]
Bindings = dict[str, list[Token]]


class Substituter:
    """Substitutes arguments into replacement lists."""

    @staticmethod
    def stringify(tokens: list[Token], start: int, end: int, space_before: bool = False) -> Token:
        """Stringify an unexpanded argument: one space wherever whitespace separated tokens.

        Each token carries whether whitespace preceded it where it was written, including
        tokens that reached the argument through another macro's replacement list.
        """
        pieces: list[str] = []
        space = False  # whitespace before a placemarker that vanishes moves to the next token
        for token in tokens:
            if token.kind == "empty":
                space = space or token.space_before
                continue
            if pieces and (token.space_before or space):
                pieces.append(" ")
            pieces.append(token.text)
            space = False
        spelling = "".join(pieces).replace("\\", "\\\\").replace('"', '\\"')
        return Token(
            f'"{spelling}"', "literal", start, end, generated=True, space_before=space_before
        )

    def substitute(
        self,
        body: list[Token],
        definition: MacroDefinition,
        raw: Bindings,
        expanded: Bindings,
        start: int,
        end: int,
        va_opts: dict[str, list[Token]] | None = None,
    ) -> list[Token]:
        """Apply #/## and parameter substitution before the replacement rescan."""
        if va_opts:
            raw, expanded = self._bind_va_opts(definition, raw, expanded, start, end, va_opts)
        elements = self._elements(body, definition, raw, expanded, start, end)
        return paste_elements(elements, definition, start, end)

    def _bind_va_opts(
        self,
        definition: MacroDefinition,
        raw: Bindings,
        expanded: Bindings,
        start: int,
        end: int,
        va_opts: dict[str, list[Token]],
    ) -> tuple[Bindings, Bindings]:
        """Bind each ``__VA_OPT__`` group to its substituted content, or to nothing."""
        va_args = expanded.get("__VA_ARGS__", [])
        active = any(token.kind != "empty" for token in va_args)
        raw = dict(raw)
        expanded = dict(expanded)
        for binding, content in va_opts.items():
            replacement = (
                self.substitute(content, definition, raw, expanded, start, end) if active else []
            )
            raw[binding] = replacement
            expanded[binding] = replacement
        return raw, expanded

    def _elements(
        self,
        body: list[Token],
        definition: MacroDefinition,
        raw: Bindings,
        expanded: Bindings,
        start: int,
        end: int,
    ) -> list[Element]:
        """Replace parameters and apply ``#``; leave ``##`` as markers for pasting."""
        parameters = set(raw)
        elements: list[Element] = []
        index = 0
        while index < len(body):
            part = body[index]
            if is_stringify_operator(part) and definition.parameters is not None:
                operand = body[index + 1] if index + 1 < len(body) else None
                if (
                    operand is None
                    or operand.kind not in {"identifier", VA_OPT_KIND}
                    or operand.text not in parameters
                ):
                    raise ExpansionError(
                        f"stringification operator in {definition.name} is not followed by a parameter"
                    )
                elements.append([self.stringify(raw[operand.text], start, end, part.space_before)])
                index += 2
                continue
            if is_paste_operator(part):
                elements.append(_PASTE)
            elif part.kind in {"identifier", VA_OPT_KIND} and part.text in parameters:
                adjacent_paste = (index > 0 and is_paste_operator(body[index - 1])) or (
                    index + 1 < len(body) and is_paste_operator(body[index + 1])
                )
                value = list(raw[part.text] if adjacent_paste else expanded[part.text])
                if adjacent_paste and not value:
                    value = [Token("", "empty", start, end, generated=True)]
                elif value and value[0].space_before != part.space_before:
                    # The argument's first token is spaced like the parameter it replaces.
                    value[0] = replace(value[0], space_before=part.space_before)
                elements.append(value)
            else:
                elements.append([part])
            index += 1
        return elements


def paste_tokens(
    left: list[Token], right: list[Token], definition: MacroDefinition, start: int, end: int
) -> list[Token]:
    """Paste boundary tokens while preserving placemarkers until rescanning."""
    if not left:
        return right
    if not right:
        return left
    left_token = left[-1]
    right_token = right[0]
    if left_token.kind == "empty" and right_token.kind == "empty":
        marker = Token("", "empty", start, end, left_token.hidden | right_token.hidden, True)
        return left[:-1] + [marker] + right[1:]
    if left_token.kind == "empty":
        return left[:-1] + right
    if right_token.kind == "empty":
        return left + right[1:]
    text = left_token.text + right_token.text
    tokens = [token for token in tokenize(text) if token.kind not in {"space", "comment"}]
    if len(tokens) != 1 or tokens[0].text != text:
        raise ExpansionError(
            f"invalid token paste in {definition.name}: {left_token.text!r} ## {right_token.text!r}"
        )
    merged = Token(
        text,
        tokens[0].kind,
        start,
        end,
        left_token.hidden | right_token.hidden,
        True,
        left_token.space_before,
    )
    return left[:-1] + [merged] + right[1:]


def paste_elements(
    elements: list[Element], definition: MacroDefinition, start: int, end: int
) -> list[Token]:
    """Join the elements, applying every ``##`` marker between its neighbours."""
    if not elements:
        return []
    first, last = elements[0], elements[-1]
    if first == _PASTE or last == _PASTE:
        raise ExpansionError(f"token paste operator cannot appear at an edge of {definition.name}")
    if not isinstance(first, list):
        raise ExpansionError(f"invalid token paste placement in {definition.name}")
    result = first
    index = 1
    while index < len(elements):
        item = elements[index]
        if item == _PASTE:
            right = elements[index + 1] if index + 1 < len(elements) else _PASTE
            if not isinstance(right, list):
                raise ExpansionError(f"invalid token paste placement in {definition.name}")
            result = paste_tokens(result, right, definition, start, end)
            index += 2
        else:
            assert isinstance(item, list)
            result.extend(item)
            index += 1
    return result
