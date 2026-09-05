import cpre
import pytest


def test_ordinary_expression_completes_below_default_limits():
    result = cpre.analyze_source("#if A && B\n#endif\n")

    assert result.complete
    assert result.incomplete == ()


def test_atom_limit_returns_structured_incomplete_result():
    result = cpre.analyze_source(
        "#if A && B && C\n#endif\n",
        filename="atoms.c",
        options=cpre.AnalysisOptions(max_atoms=2),
    )

    assert not result.complete
    assert result.findings == ()
    assert len(result.incomplete) == 1
    diagnostic = result.incomplete[0]
    assert diagnostic.code is cpre.ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert diagnostic.resource == "atoms"
    assert diagnostic.limit == 2
    assert diagnostic.observed == 3
    assert diagnostic.location is None
    assert result.filename == "atoms.c"


def test_higher_atom_limit_allows_same_source_to_complete():
    source = "#if A && B && C\n#endif\n"

    limited = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))
    allowed = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=3))

    assert not limited.complete
    assert allowed.complete


def test_bdd_node_limit_is_deterministic_and_located():
    options = cpre.AnalysisOptions(max_bdd_nodes=1)
    source = "#if A && B\n#endif\n"

    first = cpre.analyze_source(source, options=options)
    second = cpre.analyze_source(source, options=options)

    assert first.incomplete == second.incomplete
    diagnostic = first.incomplete[0]
    assert diagnostic.resource == "bdd_nodes"
    assert diagnostic.limit == 1
    assert diagnostic.observed == 2
    assert diagnostic.location == cpre.SourceLocation(line=1)
    assert first.findings == ()


def test_work_limit_returns_no_partial_finding():
    source = "#if A || B\n#elif A\n#endif\n"
    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_work=1))

    assert not result.complete
    assert result.incomplete[0].resource == "work"
    assert result.incomplete[0].location == cpre.SourceLocation(line=1)
    assert result.findings == ()


def test_higher_node_and_work_limits_allow_dead_branch_proof():
    source = "#if A || B\n#elif A\n#endif\n"
    result = cpre.analyze_source(
        source,
        options=cpre.AnalysisOptions(max_bdd_nodes=100, max_work=10_000),
    )

    assert result.complete
    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


@pytest.mark.parametrize("name", ["max_atoms", "max_bdd_nodes", "max_work"])
def test_analysis_options_require_positive_integer_limits(name):
    values = {"max_atoms": 64, "max_bdd_nodes": 100_000, "max_work": 500_000}
    values[name] = 0

    with pytest.raises(cpre.AnalysisError) as caught:
        cpre.AnalysisOptions(**values)

    assert caught.value.code is cpre.ErrorCode.ANALYSIS_FAILURE
