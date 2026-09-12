import pytest

from cpre import ErrorCode, SourceLocation, preprocess_source


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
