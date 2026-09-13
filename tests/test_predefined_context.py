import pytest

from cpre import (
    AnalysisError,
    ErrorCode,
    MacroConfiguration,
    PreprocessingContext,
    SourceLocation,
    UnknownNamePolicy,
    preprocess_source,
)
from cpre.expansion import tokenize


def _tokens(source: str) -> tuple[str, ...]:
    return tuple(
        token.text
        for token in tokenize(source)
        if token.kind not in {"space", "comment"}
    )


def test_line_expands_from_physical_lines_without_extra_configuration():
    source = "int first = __LINE__;\nint second = __LINE__;\n"
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert _tokens(result.source) == (
        "int", "first", "=", "1", ";",
        "int", "second", "=", "2", ";",
    )


def test_file_uses_explicit_filename_and_missing_filename_is_atomic():
    source = "const char *file = __FILE__;\n"
    result = preprocess_source(source, filename="src/example.c")
    assert result.complete, result.incomplete
    assert '"src/example.c"' in _tokens(result.source)

    missing = preprocess_source(source)
    assert not missing.complete
    assert missing.source is missing.source_map is missing.macros is None
    diagnostic, = missing.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(1, 20)


def test_repeated_line_directives_update_logical_line_and_file_only():
    source = (
        "int before = __LINE__;\n"
        "#line 100\n"
        "int mapped = __LINE__;\n"
        "#line 7 \"logical.c\"\n"
        "const char *file = __FILE__;\n"
        "int next = __LINE__;\n"
    )
    result = preprocess_source(source, filename="physical.c")
    assert result.complete, result.incomplete
    tokens = _tokens(result.source)
    assert ("before", "=", "1") == tokens[1:4]
    assert ("mapped", "=", "100") in tuple(
        tokens[index:index + 3] for index in range(len(tokens) - 2)
    )
    assert '"logical.c"' in tokens
    assert ("next", "=", "8") in tuple(
        tokens[index:index + 3] for index in range(len(tokens) - 2)
    )


def test_line_operands_are_macro_expanded_before_interpretation():
    source = (
        "#define NEXT 42\n"
        "#define FILE_NAME \"mapped.c\"\n"
        "#line NEXT FILE_NAME\n"
        "int line = __LINE__;\n"
        "const char *file = __FILE__;\n"
    )
    result = preprocess_source(source, filename="physical.c")
    assert result.complete, result.incomplete
    tokens = _tokens(result.source)
    assert "42" in tokens
    assert '"mapped.c"' in tokens


def test_line_and_file_can_expand_inside_line_directive_operands():
    source = (
        "#line __LINE__ \"first.c\"\n"
        "#line 50 __FILE__\n"
        "int line = __LINE__;\n"
        "const char *file = __FILE__;\n"
    )
    result = preprocess_source(source, filename="physical.c")
    assert result.complete, result.incomplete
    tokens = _tokens(result.source)
    assert "50" in tokens
    assert '"first.c"' in tokens


def test_logical_line_changes_do_not_change_physical_source_mapping():
    source = "#line 900 \"logical.c\"\nint value = __LINE__;\n"
    result = preprocess_source(source, filename="physical.c")
    assert result.complete, result.incomplete
    expanded = [mapping for mapping in result.source_map if mapping.expanded]
    assert len(expanded) == 1
    mapping = expanded[0]
    assert mapping.start == SourceLocation(2, 13)
    assert source[mapping.source_start:mapping.source_end] == "__LINE__"
    assert "900" in result.source[mapping.output_start:mapping.output_end]
    assert result.filename == "physical.c"


def test_standard_environment_values_are_explicit_and_deterministic():
    context = PreprocessingContext(standard_macros={
        "__STDC__": "1",
        "__STDC_VERSION__": "202311L",
        "__STDC_HOSTED__": "1",
        "__DATE__": '"Sep 12 2026"',
        "__TIME__": '"20:14:00"',
    })
    source = (
        "#if __STDC__ && __STDC_HOSTED__ && __STDC_VERSION__ >= 202311L\n"
        "int selected;\n"
        "#endif\n"
        "const char *date = __DATE__;\n"
        "const char *time = __TIME__;\n"
    )
    first = preprocess_source(source, context=context)
    second = preprocess_source(source, context=context)
    assert first.complete, first.incomplete
    assert second.complete, second.incomplete
    assert first.source == second.source
    assert "selected" in first.source
    tokens = _tokens(first.source)
    assert '"Sep 12 2026"' in tokens
    assert '"20:14:00"' in tokens
    assert first.macros["__STDC_VERSION__"].definition.replacement == "202311L"


def test_unconfigured_standard_environment_value_remains_atomic_in_condition():
    result = preprocess_source(
        "#if __STDC_VERSION__ >= 201112L\nint selected;\n#endif\n"
    )
    assert not result.complete
    assert result.source is result.source_map is result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(1)
    assert "__STDC_VERSION__" in diagnostic.message


def test_line_builtin_works_with_closed_world_configuration():
    result = preprocess_source(
        "#if __LINE__ == 1\nint selected;\n#endif\n",
        configuration=MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED),
    )
    assert result.complete, result.incomplete
    assert "selected" in result.source


def test_inactive_line_directive_does_not_change_logical_state():
    source = (
        "#if 0\n"
        "#line 400 \"inactive.c\"\n"
        "#endif\n"
        "int line = __LINE__;\n"
        "const char *file = __FILE__;\n"
    )
    result = preprocess_source(source, filename="physical.c")
    assert result.complete, result.incomplete
    tokens = _tokens(result.source)
    assert "4" in tokens
    assert '"physical.c"' in tokens
    assert '"inactive.c"' not in tokens


@pytest.mark.parametrize(
    "directive",
    [
        "#line 0",
        "#line -1",
        "#line 12 L\"wide.c\"",
        "#line 12 \"a.c\" extra",
    ],
)
def test_malformed_or_nonstandard_line_forms_are_structured_incomplete(directive):
    result = preprocess_source(directive + "\nint kept;\n")
    assert not result.complete
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert diagnostic.location == SourceLocation(1)


def test_context_rejects_vendor_and_location_sensitive_names():
    for name in ("__GNUC__", "__clang__", "__LINE__", "__FILE__"):
        with pytest.raises(AnalysisError) as caught:
            PreprocessingContext(standard_macros={name: "1"})
        assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_context_conflict_with_existing_macro_configuration_is_rejected():
    context = PreprocessingContext(standard_macros={"__STDC__": "1"})
    with pytest.raises(AnalysisError) as caught:
        preprocess_source(
            "",
            configuration=MacroConfiguration(integers={"__STDC__": 1}),
            context=context,
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
