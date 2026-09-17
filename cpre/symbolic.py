"""Stable symbolic-expression helpers exposed through :mod:`cpre`.

The implementation model continues to live in internal modules. Downstream
consumers should import these names from the top-level :mod:`cpre` package,
which is the compatibility boundary documented by the project.
"""

from __future__ import annotations

from .expressions import (
    conjunction as _conjunction,
    disjunction as _disjunction,
    expression_atoms as _expression_atoms,
    expression_comparison_key as _expression_comparison_key,
    expression_predicates as _expression_predicates,
    format_expression as _format_expression,
    negate as _negate,
    simplify as _simplify,
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
    # Preserve display-oriented ordering while adding a semantic tie-breaker
    # for nodes that render identically, such as Variable("A") and
    # Predicate("A").
    return (_format_expression(expression), _expression_comparison_key(expression))


def _canonicalize_order(expression: Expression) -> Expression:
    if isinstance(expression, Negation):
        return Negation(_canonicalize_order(expression.operand))
    if isinstance(expression, (Conjunction, Disjunction)):
        operands = tuple(_canonicalize_order(item) for item in expression.operands)
        cls = Conjunction if isinstance(expression, Conjunction) else Disjunction
        return cls(tuple(sorted(operands, key=_sort_key)))
    return expression


def simplify(expression: Expression) -> Expression:
    """Apply local Boolean identities without SAT/integer reasoning."""

    _require_expression(expression)
    return _canonicalize_order(_simplify(expression))


def normalize(expression: Expression) -> Expression:
    """Canonicalize association, ordering, identities, duplicates and absorption."""

    return simplify(expression)


def negate(expression: Expression) -> Expression:
    """Return the normalized Boolean negation of *expression*."""

    _require_expression(expression)
    return normalize(_negate(expression))


def conjunction(*expressions: Expression) -> Expression:
    """Build a normalized conjunction using deterministic Boolean identities."""

    for expression in expressions:
        _require_expression(expression)
    return normalize(_conjunction(*expressions))


def disjunction(*expressions: Expression) -> Expression:
    """Build a normalized disjunction using deterministic Boolean identities."""

    for expression in expressions:
        _require_expression(expression)
    return normalize(_disjunction(*expressions))


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

    _require_expression(expression)
    return _expression_predicates(expression)


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
