import pytest

import cpre


def _find(result, kind, line):
    return next(
        finding
        for finding in result.findings
        if finding.kind is kind and finding.location.line == line
    )


def test_no_assumptions_preserve_symbolic_behavior():
    source = "#if FEATURE\n#endif\n"
    assert cpre.analyze_source(source).findings == ()


def test_known_defined_macro_makes_ifdef_redundant():
    result = cpre.analyze_source(
        "#ifdef FEATURE\n#endif\n",
        assumptions=cpre.MacroAssumptions(defined={"FEATURE"}),
    )
    finding = _find(result, cpre.FindingKind.REDUNDANT_BRANCH, 1)
    assert finding.depends_on_assumptions is True


def test_known_undefined_macro_makes_ifdef_dead():
    result = cpre.analyze_source(
        "#ifdef FEATURE\n#endif\n",
        assumptions=cpre.MacroAssumptions(undefined={"FEATURE"}),
    )
    finding = _find(result, cpre.FindingKind.DEAD_BRANCH, 1)
    assert finding.depends_on_assumptions is True


def test_definedness_and_boolean_value_are_distinct():
    source = "#if defined(FEATURE) && FEATURE\n#endif\n"
    result = cpre.analyze_source(
        source,
        assumptions=cpre.MacroAssumptions(
            defined={"FEATURE"},
            values={"FEATURE": False},
        ),
    )
    finding = _find(result, cpre.FindingKind.DEAD_BRANCH, 1)
    assert finding.depends_on_assumptions is True


def test_mapping_shorthand_constrains_bare_boolean_macro_only():
    source = "#if FEATURE && OTHER\n#endif\n"
    result = cpre.analyze_source(source, assumptions={"FEATURE": True})
    finding = _find(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION, 1)
    assert finding.exact_simplification == cpre.ExactSimplification(
        original="FEATURE && OTHER",
        replacement="OTHER",
    )
    assert finding.depends_on_assumptions is True


def test_unknown_macro_remains_symbolic_with_assumptions():
    source = "#if KNOWN && UNKNOWN\n#endif\n"
    result = cpre.analyze_source(source, assumptions={"KNOWN": True})
    finding = _find(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION, 1)
    assert finding.exact_simplification.replacement == "UNKNOWN"


def test_nested_condition_under_assumptions():
    source = "#if OUTER\n#if INNER\n#endif\n#endif\n"
    result = cpre.analyze_source(source, assumptions={"OUTER": False})
    nested = _find(result, cpre.FindingKind.DEAD_BRANCH, 2)
    assert nested.depends_on_assumptions is True


def test_elif_coverage_under_assumptions():
    source = "#if A\n#elif B\n#else\n#endif\n"
    result = cpre.analyze_source(source, assumptions={"A": False, "B": True})
    assert _find(result, cpre.FindingKind.DEAD_BRANCH, 1).depends_on_assumptions
    assert _find(result, cpre.FindingKind.REDUNDANT_BRANCH, 2).depends_on_assumptions
    assert _find(result, cpre.FindingKind.DEAD_BRANCH, 3).depends_on_assumptions


def test_universal_finding_is_not_marked_assumption_dependent():
    source = "#if A && A\n#endif\n"
    result = cpre.analyze_source(source, assumptions={"UNRELATED": True})
    finding = _find(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION, 1)
    assert finding.depends_on_assumptions is False


def test_contradictory_definedness_is_structured_error():
    with pytest.raises(cpre.AnalysisError) as caught:
        cpre.MacroAssumptions(defined={"FEATURE"}, undefined={"FEATURE"})
    assert caught.value.code is cpre.ErrorCode.INVALID_ASSUMPTIONS


def test_undefined_true_value_is_rejected():
    with pytest.raises(cpre.AnalysisError) as caught:
        cpre.MacroAssumptions(undefined={"FEATURE"}, values={"FEATURE": True})
    assert caught.value.code is cpre.ErrorCode.INVALID_ASSUMPTIONS


def test_assumption_order_does_not_affect_results():
    source = "#if A && B && C\n#endif\n"
    first = cpre.analyze_source(
        source,
        assumptions=cpre.MacroAssumptions(
            defined={"D", "E"},
            values={"A": True, "B": False},
        ),
    )
    second = cpre.analyze_source(
        source,
        assumptions=cpre.MacroAssumptions(
            defined={"E", "D"},
            values={"B": False, "A": True},
        ),
    )
    assert first == second
