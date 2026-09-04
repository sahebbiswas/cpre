import pytest
from hypothesis import given, settings, strategies as st

from cpre import cpre as conditions


VARIABLES = tuple(conditions.Variable(name) for name in "ABCD")


@st.composite
def compound_expressions(draw, children):
    operands = tuple(draw(st.lists(children, min_size=2, max_size=4)))
    constructor = draw(st.sampled_from((conditions.Conjunction, conditions.Disjunction)))
    return constructor(operands)


BOOLEAN_EXPRESSIONS = st.recursive(
    st.sampled_from((conditions.FALSE, conditions.TRUE, *VARIABLES)),
    lambda children: st.one_of(
        children.map(conditions.Negation),
        compound_expressions(children),
    ),
    max_leaves=12,
)


def bdd_for(expression):
    return conditions._BDD(conditions._expression_atoms_in_order(expression))


@given(BOOLEAN_EXPRESSIONS)
@settings(max_examples=200, deadline=None)
def test_algebraic_simplification_preserves_boolean_value(expression):
    bdd = bdd_for(expression)

    simplified = conditions.simplify(expression)

    assert bdd.equivalent_under(conditions.TRUE, expression, simplified)


@given(BOOLEAN_EXPRESSIONS)
@settings(max_examples=200, deadline=None)
def test_exact_simplification_preserves_boolean_value(expression):
    bdd = bdd_for(expression)

    simplified = conditions.exact_simplify(expression, bdd)

    assert bdd.equivalent_under(conditions.TRUE, expression, simplified)


@pytest.mark.parametrize(
    "condition,expected_status",
    [
        ("0", "dead"),
        ("A || !A", "redundant"),
        ("A", "reachable"),
    ],
)
def test_branch_status_regressions(condition, expected_status):
    tree = conditions.analyze_source(f"#if {condition}\n#endif\n")

    assert tree.groups[0].branches[0].analysis.status == expected_status
