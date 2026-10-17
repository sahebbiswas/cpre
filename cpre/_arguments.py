"""Argument collection and binding: the second stage of macro expansion.

``collect_arguments`` reads the parenthesized argument list of an invocation from the
token stream; ``bind_arguments`` checks it against the macro's parameters (folding the
variadic tail into ``__VA_ARGS__``) and names each argument.
"""

from __future__ import annotations

from collections import deque

from ._replacement import ExpansionError
from .lexer import Token
from .macros import MacroDefinition
from .robdd import AnalysisBudget


def collect_arguments(
    pending: deque[Token], name: str, budget: AnalysisBudget
) -> tuple[list[list[Token]], list[Token], Token]:
    """Consume ``( ... )`` from ``pending``: the arguments, the commas between them and the ``)``."""
    pending.popleft()  # opening parenthesis
    arguments: list[list[Token]] = [[]]
    separators: list[Token] = []
    depth = 0
    while pending:
        token = pending.popleft()
        budget.consume()
        if token.text == ")" and depth == 0:
            return arguments, separators, token
        if token.text == "," and depth == 0:
            separators.append(token)
            arguments.append([])
            continue
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        arguments[-1].append(token)
    raise ExpansionError(f"unterminated invocation of {name} (possibly interrupted by a directive)")


def bind_arguments(
    definition: MacroDefinition, arguments: list[list[Token]], separators: list[Token]
) -> dict[str, list[Token]]:
    """Name the collected arguments after the macro's parameters, in parameter order."""
    parameters = definition.parameters or ()
    if not parameters and not definition.variadic and arguments == [[]]:
        arguments = []
    if definition.variadic:
        # The variadic argument may be empty (`F(x, )`) or left out with its comma
        # (`F(x)`, C23 and C++20; GCC and Clang accept it in every mode). Either way it
        # binds as an empty `__VA_ARGS__`.
        if len(arguments) < len(parameters):
            raise ExpansionError(
                f"{definition.name} expects at least {len(parameters)} arguments, "
                f"got {len(arguments)}"
            )
        if len(arguments) == len(parameters):
            arguments = [*arguments, []]
        extra: list[Token] = []
        for argument_index, argument in enumerate(arguments[len(parameters) :]):
            if argument_index:
                extra.append(separators[len(parameters) + argument_index - 1])
            extra.extend(argument)
        arguments = arguments[: len(parameters)] + [extra]
        parameters = (*parameters, "__VA_ARGS__")
    elif len(arguments) != len(parameters):
        raise ExpansionError(
            f"{definition.name} expects {len(parameters)} arguments, got {len(arguments)}"
        )
    return dict(zip(parameters, arguments))
