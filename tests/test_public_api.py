import cpre
import pytest


def _by_kind(result, kind):
    return [finding for finding in result.findings if finding.kind is kind]


def test_top_level_public_api_exposes_supported_symbols_only():
    assert cpre.__all__ == [
        "AnalysisResult",
        "ConditionError",
        "ConditionalTree",
        "Finding",
        "FindingKind",
        "SourceLocation",
        "__version__",
        "analyze_source",
    ]


def test_analyze_source_returns_structured_result_and_filename():
    result = cpre.analyze_source("#if FEATURE\n#endif\n", filename="feature.c")

    assert isinstance(result, cpre.AnalysisResult)
    assert isinstance(result.tree, cpre.ConditionalTree)
    assert result.filename == "feature.c"
    assert result.findings == ()


def test_invalid_source_raises_public_condition_error():
    with pytest.raises(cpre.ConditionError):
        cpre.analyze_source("#elif FEATURE\n")


def test_dead_branch_is_reported_structurally():
    result = cpre.analyze_source(
        "#if A || B\n#elif A\n#endif\n",
        filename="dead.c",
    )

    findings = _by_kind(result, cpre.FindingKind.DEAD_BRANCH)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.location == cpre.SourceLocation(line=2)
    assert finding.directive == "elif"
    assert finding.original_condition == "A"
    assert finding.reason == "condition contradicts its parent or earlier branches"


def test_redundant_branch_is_reported_structurally():
    result = cpre.analyze_source(
        "#if PARENT\n#if PARENT || CHILD\n#endif\n#endif\n"
    )

    findings = _by_kind(result, cpre.FindingKind.REDUNDANT_BRANCH)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.location == cpre.SourceLocation(line=2)
    assert finding.contextual_condition == "1"


def test_exact_simplification_is_reported_structurally():
    result = cpre.analyze_source("#if (A && B) || (A && !B)\n#endif\n")

    findings = _by_kind(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.original_condition == "(A && B) || (A && !B)"
    assert finding.simplified_condition == "A"


def test_contextual_simplification_is_reported_separately():
    result = cpre.analyze_source(
        "#if PARENT\n#if PARENT && CHILD\n#endif\n#endif\n"
    )

    findings = _by_kind(result, cpre.FindingKind.CONTEXTUAL_SIMPLIFICATION)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.location == cpre.SourceLocation(line=2)
    assert finding.contextual_condition == "CHILD"


def test_opaque_predicates_are_preserved_in_findings():
    result = cpre.analyze_source("#if VERSION >= 4 && FLAG && FLAG\n#endif\n")

    findings = _by_kind(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION)
    assert len(findings) == 1
    assert findings[0].opaque_predicates == ("VERSION >= 4",)


def test_formatting_only_normalization_does_not_create_a_finding():
    result = cpre.analyze_source("#if defined(FEATURE)\n#endif\n")

    assert result.findings == ()
