import pytest

from cpre import (
    ErrorCode, MacroConfiguration, SourceLocation, UnknownNamePolicy,
    preprocess_source,
)


@pytest.mark.parametrize('source', [
    '#define HERE __LINE__\n#if HERE > 0\nint kept;\n#endif\n',
    '#define HERE() __LINE__\n#if HERE() > 0\nint kept;\n#endif\n',
])
def test_predefined_macro_reached_through_condition_expansion_is_unsupported(source):
    result = preprocess_source(source)

    assert not result.complete
    assert result.source is result.source_map is result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(2)
    assert '__LINE__' in diagnostic.message


@pytest.mark.parametrize('source', [
    '#ifdef __cplusplus\nint cpp;\n#endif\n',
    '#ifndef __cplusplus\nint c;\n#endif\n',
    '#if defined(__STDC_VERSION__)\nint modern;\n#endif\n',
    '#if !defined __STDC_VERSION__\nint fallback;\n#endif\n',
])
def test_predefined_macro_definedness_stays_open_world(source):
    result = preprocess_source(source)

    assert not result.complete
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNRESOLVED_CONDITION
    assert diagnostic.location == SourceLocation(1)


def test_predefined_macro_definedness_respects_closed_world_configuration():
    source = '#ifdef __cplusplus\nint cpp;\n#else\nint c;\n#endif\n'
    result = preprocess_source(
        source,
        configuration=MacroConfiguration(unknown_names=UnknownNamePolicy.UNDEFINED),
    )

    assert result.complete, result.incomplete
    assert 'int cpp;' not in result.source
    assert 'int c;' in result.source


def test_explicit_predefined_macro_definition_supports_value_and_definedness():
    source = '#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 202311L\nint modern;\n#endif\n'
    result = preprocess_source(
        source,
        configuration=MacroConfiguration(integers={'__STDC_VERSION__': 202311}),
    )

    assert result.complete, result.incomplete
    assert 'int modern;' in result.source
