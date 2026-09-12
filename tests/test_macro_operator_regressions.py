from cpre import preprocess_source
from cpre.expansion import tokenize


def spellings(source):
    return [token.text for token in tokenize(source) if token.kind not in {'space', 'comment'}]


def test_stringified_variadic_arguments_preserve_separator_whitespace():
    source = '#define V(...) #__VA_ARGS__\nV(a, b) V(a,b) V(a, /* gap */ b)'
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == ['"a, b"', '"a,b"', '"a, b"']
