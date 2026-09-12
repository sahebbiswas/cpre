from cpre import ErrorCode, SourceLocation, preprocess_source


def test_va_opt_in_unexpanded_macro_argument_is_rejected():
    result = preprocess_source('#define S(x) #x\nS(__VA_OPT__(x))')

    assert result.source is result.source_map is result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(2, 3)
    assert 'only valid in a variadic macro replacement list' in diagnostic.message


def test_va_opt_in_discarded_ordinary_source_is_ignored():
    result = preprocess_source('#if 0\n__VA_OPT__(x)\n#endif\nint ok;')

    assert result.complete, result.incomplete
