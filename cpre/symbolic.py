"""Stable symbolic-expression helpers exposed through :mod:`cpre`.

The implementation model continues to live in internal modules. Downstream
consumers should import these names from the top-level :mod:`cpre` package,
which is the compatibility boundary documented by the project.
"""

from __future__ import annotations

from .expressions import (
    expression_atoms as _expression_atoms,
    expression_comparison_key as _expression_comparison_key,
    format_expression as _format_expression,
)
from .model import (
    BooleanAtom,
    Conjunction,
    Constant,
    DefinedVariable,
    Disjunction,
    Expression,
    FALSE,
    Negation,
    Predicate,
    TRUE,
    Variable,
)

_EXPRESSION_TYPES = (
    Constant,
    Variable,
    Predicate,
    Negation,
    Conjunction,
    Disjunction,
)


def _require_expression(expression: Expression) -> None:
    if not isinstance(expression, _EXPRESSION_TYPES):
        raise TypeError("expected a symbolic expression")


def _sort_key(expression: Expression) -> tuple[object, ...]:
    # Preserve the established display-oriented ordering while ensuring nodes
    # with identical rendered text (for example Variable("A") and
    # Predicate("A")) have a stable semantic tie-breaker.
    return (_format_expression(expression), _expression_comparison_key(expression))


def negate(expression: Expression) -> Expression:
    """Return the normalized Boolean negation of *expression*."""

    expression = simplify(expression)
    if isinstance(expression, Constant):
        return Constant(not expression.value)
    if isinstance(expression, Negation):
        return expression.operand
    return Negation(expression)


def conjunction(*expressions: Expression) -> Expression:
    """Build a normalized conjunction using deterministic Boolean identities."""

    operands: list[Expression] = []
    for expression in expressions:
        expression = simplify(expression)
        if expression == FALSE:
            return FALSE
        if expression == TRUE:
            continue
        operands.extend(
            expression.operands if isinstance(expression, Conjunction) else (expression,)
        )

    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return FALSE

    filtered = [
        operand
        for operand in unique
        if not (
            isinstance(operand, Disjunction)
            and any(term in unique for term in operand.operands)
        )
    ]
    if not filtered:
        return TRUE
    if len(filtered) == 1:
        return filtered[0]
    return Conjunction(tuple(sorted(filtered, key=_sort_key)))


def disjunction(*expressions: Expression) -> Expression:
    """Build a normalized disjunction using deterministic Boolean identities."""

    operands: list[Expression] = []
    for expression in expressions:
        expression = simplify(expression)
        if expression == TRUE:
            return TRUE
        if expression == FALSE:
            continue
        operands.extend(
            expression.operands if isinstance(expression, Disjunction) else (expression,)
        )

    unique = set(operands)
    if any(negate(operand) in unique for operand in unique):
        return TRUE

    filtered = [
        operand
        for operand in unique
        if not (
            isinstance(operand, Conjunction)
            and any(term in unique for term in operand.operands)
        )
    ]
    if not filtered:
        return FALSE
    if len(filtered) == 1:
        return filtered[0]
    return Disjunction(tuple(sorted(filtered, key=_sort_key)))


def simplify(expression: Expression) -> Expression:
    """Apply local Boolean identities without performing SAT/integer reasoning."""

    _require_expression(expression)
    if isinstance(expression, (Constant, Variable, Predicate)):
        return expression
    if isinstance(expression, Negation):
        return negate(expression.operand)
    if isinstance(expression, Conjunction):
        return conjunction(*expression.operands)
    return disjunction(*expression.operands)


def normalize(expression: Expression) -> Expression:
    """Canonicalize association, ordering, identities, duplicates and absorption."""

    return simplify(expression)


def format_expression(expression: Expression) -> str:
    """Render canonical Boolean structure with preprocessor-style operators."""

    return _format_expression(normalize(expression))


def ordered_atoms(expression: Expression) -> tuple[BooleanAtom, ...]:
    """Return unique referenced atoms in a deterministic semantic order.

    Enumeration intentionally uses the original expression rather than its
    simplified form so callers can inventory every atom referenced by input.
    Macro value and macro definedness remain distinct atoms.
    """

    _require_expression(expression)
    return tuple(sorted(_expression_atoms(expression), key=_expression_comparison_key))


def expression_predicates(expression: Expression) -> set[str]:
    """Return opaque predicate text without interpreting identifiers inside it."""

    return {
        atom.text
        for atom in ordered_atoms(expression)
        if isinstance(atom, Predicate)
    }


def expression_to_dict(expression: Expression) -> dict[str, object]:
    """Return a canonical tagged JSON-compatible representation."""

    return _to_dict(normalize(expression))


def _to_dict(expression: Expression) -> dict[str, object]:
    if isinstance(expression, Constant):
        return {"kind": "constant", "value": expression.value}
    if isinstance(expression, DefinedVariable):
        return {"kind": "defined", "name": expression.name}
    if isinstance(expression, Variable):
        return {"kind": "variable", "name": expression.name}
    if isinstance(expression, Predicate):
        return {"kind": "predicate", "text": expression.text}
    if isinstance(expression, Negation):
        return {"kind": "not", "operand": _to_dict(expression.operand)}
    if isinstance(expression, Conjunction):
        return {
            "kind": "and",
            "operands": [_to_dict(item) for item in expression.operands],
        }
    return {
        "kind": "or",
        "operands": [_to_dict(item) for item in expression.operands],
    }


def _require_exact_fields(data: dict[object, object], *fields: str) -> None:
    expected = {"kind", *fields}
    if set(data) != expected:
        raise ValueError("unknown expression fields or missing required fields")


def expression_from_dict(data: object) -> Expression:
    """Deserialize tagged expression data and return its canonical form.

    Unknown kinds, missing/extra fields, and values with the wrong JSON shape
    are rejected rather than coerced.
    """

    return normalize(_from_dict(data))


def _from_dict(data: object) -> Expression:
    if not isinstance(data, dict):
        raise ValueError("expression must be an object")

    kind = data.get("kind")
    if not isinstance(kind, str):
        raise ValueError("expression kind must be a string")

    if kind == "constant":
        _require_exact_fields(data, "value")
        value = data["value"]
        if type(value) is not bool:
            raise ValueError("constant value must be a bool")
        return Constant(value)

    if kind in {"variable", "defined"}:
        _require_exact_fields(data, "name")
        name = data["name"]
        if not isinstance(name, str):
            raise ValueError("expression name must be a string")
        cls = Variable if kind == "variable" else DefinedVariable
        return cls(name)

    if kind == "predicate":
        _require_exact_fields(data, "text")
        text = data["text"]
        if not isinstance(text, str):
            raise ValueError("predicate text must be a string")
        return Predicate(text)

    if kind == "not":
        _require_exact_fields(data, "operand")
        return Negation(_from_dict(data["operand"]))

    if kind in {"and", "or"}:
        _require_exact_fields(data, "operands")
        operands = data["operands"]
        if not isinstance(operands, list):
            raise ValueError("operands must be an array")
        cls = Conjunction if kind == "and" else Disjunction
        return cls(tuple(_from_dict(item) for item in operands))

    raise ValueError(f"unknown expression kind: {kind!r}")


__all__ = [
    "BooleanAtom",
    "Conjunction",
    "Constant",
    "DefinedVariable",
    "Disjunction",
    "Expression",
    "FALSE",
    "Negation",
    "Predicate",
    "TRUE",
    "Variable",
    "conjunction",
    "disjunction",
    "expression_from_dict",
    "expression_predicates",
    "expression_to_dict",
    "format_expression",
    "negate",
    "normalize",
    "ordered_atoms",
    "simplify",
]
