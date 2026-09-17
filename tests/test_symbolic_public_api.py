import json
import os
import subprocess
import sys

import cpre
import pytest


def test_macro_truth_definedness_and_predicate_are_distinct_atoms():
    value = cpre.Variable("FEATURE")
    defined = cpre.DefinedVariable("FEATURE")
    predicate = cpre.Predicate("FEATURE")

    assert value != defined
    assert value != predicate
    assert defined != predicate
    assert set(cpre.ordered_atoms(cpre.Conjunction((value, defined, predicate)))) == {
        value,
        defined,
        predicate,
    }


def test_public_boolean_algebra_normalizes_local_identities():
    a = cpre.Variable("A")
    b = cpre.Variable("B")

    assert cpre.conjunction(a, cpre.negate(a)) == cpre.FALSE
    assert cpre.disjunction(a, cpre.negate(a)) == cpre.TRUE
    assert cpre.normalize(cpre.Conjunction((a, a, b))) == cpre.conjunction(a, b)
    assert cpre.normalize(cpre.Conjunction((a, cpre.Conjunction((b, a))))) == cpre.conjunction(a, b)
    assert cpre.normalize(cpre.Disjunction((a, cpre.Conjunction((a, b))))) == a


def test_public_formatting_normalizes_nested_structure_deterministically():
    expression = cpre.Disjunction(
        (
            cpre.Negation(cpre.DefinedVariable("DISABLED")),
            cpre.Conjunction((cpre.Variable("B"), cpre.Variable("A"))),
        )
    )

    assert cpre.format_expression(expression) == "!defined(DISABLED) || A && B"


def test_ordered_atoms_preserves_all_unsimplified_atom_categories():
    expression = cpre.Conjunction(
        (
            cpre.Variable("A"),
            cpre.Predicate("A"),
            cpre.DefinedVariable("A"),
            cpre.Variable("A"),
        )
    )

    atoms = cpre.ordered_atoms(expression)
    assert atoms == (
        cpre.DefinedVariable("A"),
        cpre.Predicate("A"),
        cpre.Variable("A"),
    )
    assert cpre.expression_predicates(expression) == {"A"}


def test_expression_serialization_round_trips_every_node_category():
    expressions = (
        cpre.TRUE,
        cpre.FALSE,
        cpre.Variable("VALUE"),
        cpre.DefinedVariable("DEFINED"),
        cpre.Predicate("VERSION >= 4"),
        cpre.Negation(cpre.Variable("NEGATED")),
        cpre.Conjunction((cpre.Variable("A"), cpre.DefinedVariable("B"))),
        cpre.Disjunction((cpre.Predicate("X > 0"), cpre.Variable("Y"))),
    )

    for expression in expressions:
        encoded = cpre.expression_to_dict(expression)
        json.dumps(encoded)
        assert cpre.expression_from_dict(encoded) == cpre.normalize(expression)


def test_expression_serialization_preserves_semantic_atom_tags():
    expression = cpre.Conjunction(
        (
            cpre.Variable("A"),
            cpre.DefinedVariable("A"),
            cpre.Predicate("A"),
        )
    )

    encoded = cpre.expression_to_dict(expression)
    kinds = {item["kind"] for item in encoded["operands"]}
    assert kinds == {"variable", "defined", "predicate"}


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"kind": 1},
        {"kind": "unknown"},
        {"kind": "constant", "value": 1},
        {"kind": "constant", "value": True, "extra": False},
        {"kind": "variable", "name": 1},
        {"kind": "defined"},
        {"kind": "predicate", "text": None},
        {"kind": "not"},
        {"kind": "and", "operands": ()},
        {"kind": "or", "operands": [{"kind": "unknown"}]},
    ],
)
def test_expression_from_dict_rejects_malformed_or_unknown_data(data):
    with pytest.raises(ValueError):
        cpre.expression_from_dict(data)


def test_public_symbolic_output_is_hash_seed_independent():
    script = r'''
import json
import cpre

expression = cpre.Conjunction((
    cpre.Variable("A"),
    cpre.Predicate("A"),
    cpre.DefinedVariable("A"),
    cpre.Variable("B"),
))
print(cpre.format_expression(expression))
print(json.dumps(cpre.expression_to_dict(expression), separators=(",", ":")))
print(repr(cpre.ordered_atoms(expression)))
'''

    outputs = []
    for seed in ("1", "2", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                env=env,
                text=True,
            )
        )

    assert outputs[0] == outputs[1] == outputs[2]
