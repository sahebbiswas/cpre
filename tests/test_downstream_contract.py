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
    cpre.MacroAssumptions,
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
    assert exact.depends_on_assumptions is False

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


def test_assumption_contract_marks_profile_specific_findings():
    result = cpre.analyze_source(
        "#ifdef FEATURE\n#endif\n",
        assumptions=cpre.MacroAssumptions(defined={"FEATURE"}),
    )
    finding = _by_kind(result, cpre.FindingKind.REDUNDANT_BRANCH)[0]
    assert finding.depends_on_assumptions is True


def test_concrete_preprocessing_installed_contract():
    result = cpre.preprocess_source(
        '#ifdef X\nint x;\n#else\nint y;\n#endif\n',
        filename='contract.c', assumptions=cpre.MacroAssumptions(defined=['X']),
    )
    assert isinstance(result, cpre.PreprocessResult)
    assert result.complete
    assert result.filename == 'contract.c'
    assert result.source.splitlines()[1] == 'int x;'
    assert 'int y;' not in result.source
    unknown = cpre.preprocess_source('#if UNKNOWN\nx\n#endif')
    assert unknown.source is None
    assert not unknown.complete
    assert isinstance(unknown.incomplete[0], cpre.PreprocessDiagnostic)
    assert unknown.incomplete[0].code is cpre.ErrorCode.UNRESOLVED_CONDITION


def test_source_order_macro_state_installed_contract():
    result = cpre.preprocess_source(
        '#define ZERO 0\n#define F(x) x\n#ifdef ZERO\nint kept;\n#endif\n'
    )
    assert result.complete and 'int kept;' in result.source
    assert isinstance(result.macros['ZERO'], cpre.MacroState)
    assert isinstance(result.macros['F'].definition, cpre.MacroDefinition)
    assert result.macros['ZERO'].defined is True
    assert result.macros['ZERO'].value is False
    assert result.macros['F'].definition.parameters == ('x',)
    env = cpre.MacroEnvironment({'X': True})
    env.undef('X')
    assert env.snapshot()['X'].defined is False


def test_object_expansion_and_source_map_installed_contract():
    source = '#define N 12345\nint a[N];\n'
    result = cpre.preprocess_source(source)
    assert result.complete
    span, = [span for span in result.source_map if span.expanded]
    assert isinstance(span, cpre.SourceMapping)
    assert source[span.source_start:span.source_end] == 'N'
    assert result.source[span.output_start:span.output_end].strip() == '12345'
    assert span.start == cpre.SourceLocation(2, 7)

    stringized = cpre.preprocess_source('#define F(x) #x\nF(1)')
    assert stringized.complete
    assert stringized.source.strip() == '"1"'
    assert stringized.macros['F'].definition.parameters == ('x',)
    assert any(span.expanded for span in stringized.source_map)


def test_function_expansion_installed_contract():
    source = '#define ADD(x, y) ((x) + (y))\nint n = ADD(1, ADD(2, 3));\n'
    result = cpre.preprocess_source(source)
    assert result.complete
    span, = [span for span in result.source_map if span.expanded]
    assert source[span.source_start:span.source_end] == 'ADD(1, ADD(2, 3))'
    assert result.source[span.output_start:span.output_end].strip() == '( ( 1 ) + ( ( ( 2 ) + ( 3 ) ) ) )'
