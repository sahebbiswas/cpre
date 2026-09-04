"""Compatibility contract for downstream analyzers using only public cpre APIs."""

import cpre


PUBLIC_IMPORTS = (
    cpre.AnalysisResult,
    cpre.ConditionalTree,
    cpre.ContextualSimplification,
    cpre.ExactSimplification,
    cpre.Finding,
    cpre.FindingKind,
    cpre.FixConfidence,
    cpre.SourceLocation,
    cpre.SourceRange,
    cpre.SuggestedEdit,
    cpre.analyze_source,
)


def _by_kind(result, kind):
    return tuple(finding for finding in result.findings if finding.kind is kind)


def test_documented_top_level_imports_are_available():
    assert all(symbol is not None for symbol in PUBLIC_IMPORTS)


def test_representative_findings_have_stable_kinds_and_ordering():
    source = """\
#if (A && B) || (A && !B)
#endif
#if ROOT
#if ROOT && CHILD
#endif
#endif
#if X || Y
#elif X
#endif
#if PARENT
#if PARENT || OTHER
#endif
#endif
#if VERSION >= 4 && FLAG && FLAG
#endif
"""

    first = cpre.analyze_source(source, filename="contract.c")
    second = cpre.analyze_source(source, filename="contract.c")

    assert first == second
    assert first.filename == "contract.c"
    assert tuple(finding.kind for finding in first.findings) == (
        cpre.FindingKind.SIMPLIFIABLE_CONDITION,
        cpre.FindingKind.CONTEXTUAL_SIMPLIFICATION,
        cpre.FindingKind.DEAD_BRANCH,
        cpre.FindingKind.REDUNDANT_BRANCH,
        cpre.FindingKind.SIMPLIFIABLE_CONDITION,
    )

    exact, contextual, dead, redundant, opaque = first.findings
    assert exact.location == cpre.SourceLocation(line=1)
    assert exact.exact_simplification == cpre.ExactSimplification(
        original="(A && B) || (A && !B)", replacement="A"
    )
    assert exact.edit is not None
    assert exact.edit.confidence is cpre.FixConfidence.EXACT

    assert contextual.location == cpre.SourceLocation(line=4)
    assert contextual.contextual_simplification == cpre.ContextualSimplification(
        original="ROOT && CHILD", replacement="CHILD"
    )
    assert contextual.edit is not None
    assert contextual.edit.confidence is cpre.FixConfidence.CONTEXTUAL

    assert dead.location == cpre.SourceLocation(line=8)
    assert dead.edit is None
    assert redundant.location == cpre.SourceLocation(line=11)
    assert redundant.edit is None

    assert opaque.location == cpre.SourceLocation(line=14)
    assert opaque.opaque_predicates == ("VERSION >= 4",)


def test_source_range_contract_uses_one_based_end_exclusive_physical_locations():
    source = "#if FLAG && \\\n    FLAG\n#endif\n"
    result = cpre.analyze_source(source)

    finding = _by_kind(result, cpre.FindingKind.SIMPLIFIABLE_CONDITION)[0]
    assert finding.edit == cpre.SuggestedEdit(
        range=cpre.SourceRange(
            start=cpre.SourceLocation(line=1, column=5),
            end=cpre.SourceLocation(line=2, column=9),
        ),
        replacement="FLAG",
        confidence=cpre.FixConfidence.EXACT,
    )


def test_macro_form_and_branch_classification_do_not_imply_mechanical_edits():
    source = "#if ROOT\n#ifdef ROOT\n#endif\n#endif\n"
    result = cpre.analyze_source(source)

    finding = _by_kind(result, cpre.FindingKind.REDUNDANT_BRANCH)[0]
    assert finding.directive == "ifdef"
    assert finding.edit is None
