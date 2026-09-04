import cpre
import pytest
from cpre import cpre as engine


@pytest.mark.parametrize(
    ("source", "code", "line"),
    [
        ("#elif FEATURE\n", cpre.ErrorCode.UNMATCHED_DIRECTIVE, 1),
        ("#if A\n#else\n#elif B\n#endif\n", cpre.ErrorCode.MISPLACED_DIRECTIVE, 3),
        ("#if A\n#else\n#else\n#endif\n", cpre.ErrorCode.MISPLACED_DIRECTIVE, 3),
        ("#if A\n#endif extra\n", cpre.ErrorCode.TRAILING_DIRECTIVE_TEXT, 2),
        ("#if A\n", cpre.ErrorCode.UNTERMINATED_CONDITIONAL, 1),
        ("#elifdef A B\n", cpre.ErrorCode.UNMATCHED_DIRECTIVE, 1),
        ("#if A\n#elifndef A B\n#endif\n", cpre.ErrorCode.MALFORMED_MACRO_DIRECTIVE, 2),
    ],
)
def test_directive_failures_have_structured_codes_and_locations(source, code, line):
    with pytest.raises(cpre.ParseError) as caught:
        cpre.analyze_source(source, filename="broken.c")

    error = caught.value
    assert error.code is code
    assert error.location is not None
    assert error.location.line == line
    assert error.filename == "broken.c"
    assert isinstance(error, cpre.CpreError)
    assert isinstance(error, cpre.ConditionError)


def test_engine_is_single_source_of_structured_directive_diagnostics():
    with pytest.raises(engine.DirectiveStructureError) as caught:
        engine.parse_source("#if A\n#else\n#elif B\n#endif\n")

    error = caught.value
    assert error.code == cpre.ErrorCode.MISPLACED_DIRECTIVE.value
    assert error.location == engine._SourceLocation(line=3, column=1)
    assert error.message == "#elif appears after #else"


def test_engine_carries_malformed_macro_code_and_location():
    with pytest.raises(engine.ExpressionSyntaxError) as caught:
        engine.parse_source("#if A\n#elifdef FEATURE EXTRA\n#endif\n")

    error = caught.value
    assert error.code == cpre.ErrorCode.MALFORMED_MACRO_DIRECTIVE.value
    assert error.location is not None
    assert error.location.line == 2
    assert error.message == "#elifdef expects exactly one macro name"


def test_malformed_expression_has_physical_location():
    with pytest.raises(cpre.ParseError) as caught:
        cpre.analyze_source("#if A &&\n#endif\n", filename="expr.c")

    error = caught.value
    assert error.code is cpre.ErrorCode.EXPRESSION_SYNTAX
    assert error.location == cpre.SourceLocation(line=1, column=7)
    assert error.filename == "expr.c"
    assert "expected an operand" in error.message


def test_continued_expression_preserves_physical_line_and_column():
    with pytest.raises(cpre.ParseError) as caught:
        cpre.analyze_source("#if A && \\\n    (B || )\n#endif\n", filename="continued.c")

    error = caught.value
    assert error.code is cpre.ErrorCode.EXPRESSION_SYNTAX
    assert error.location == cpre.SourceLocation(line=2, column=8)


def test_malformed_c23_macro_directive_is_structured():
    with pytest.raises(cpre.ParseError) as caught:
        cpre.analyze_source("#if A\n#elifdef FEATURE EXTRA\n#endif\n")

    error = caught.value
    assert error.code is cpre.ErrorCode.MALFORMED_MACRO_DIRECTIVE
    assert error.location is not None
    assert error.location.line == 2
    assert "#elifdef expects exactly one macro name" == error.message


def test_library_failure_has_no_output_side_effects(capsys):
    with pytest.raises(cpre.CpreError):
        cpre.analyze_source("#endif\n")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_public_error_keeps_engine_exception_as_cause():
    with pytest.raises(cpre.ParseError) as caught:
        cpre.analyze_source("#endif\n")

    assert caught.value.__cause__ is not None
