import pytest

from cpre import (
    AnalysisError,
    ErrorCode,
    Pragma,
    PragmaDisposition,
    PragmaOrigin,
    SourceLocation,
    preprocess_source,
)


def _handler(accepted):
    seen = []

    def handle(pragma):
        seen.append(pragma)
        return (
            PragmaDisposition.CONSUME
            if pragma.payload in accepted
            else PragmaDisposition.UNSUPPORTED
        )

    return handle, seen


def test_direct_pragma_uses_host_handler_and_is_masked():
    handler, seen = _handler({"cpre harmless"})
    result = preprocess_source(
        "#pragma cpre harmless\nint kept;\n",
        filename="unit.c",
        pragma_handler=handler,
    )

    assert result.complete
    assert "#pragma" not in result.source
    assert "int kept;" in result.source
    assert seen == [
        Pragma(
            "cpre harmless",
            PragmaOrigin.DIRECTIVE,
            SourceLocation(1, 1),
            "unit.c",
        )
    ]
    assert 1 in result.removed_lines


def test_direct_pragma_with_comments_and_splicing_has_token_payload():
    handler, seen = _handler({"cpre value more"})
    result = preprocess_source(
        "  #pragma cpre /* comment */ value \\\n"
        "    more\n"
        "int kept;\n",
        pragma_handler=handler,
    )

    assert result.complete
    assert seen[0].payload == "cpre value more"
    assert seen[0].location == SourceLocation(1, 3)


def test_direct_pragma_operator_is_destringized_and_masked():
    handler, seen = _handler({'message("hello") path\\name'})
    source = '_Pragma("message(\\\"hello\\\") path\\\\name")\nint kept;\n'

    result = preprocess_source(source, pragma_handler=handler)

    assert result.complete
    assert "_Pragma" not in result.source
    assert "int kept;" in result.source
    assert seen == [
        Pragma(
            'message("hello") path\\name',
            PragmaOrigin.OPERATOR,
            SourceLocation(1, 1),
            None,
        )
    ]


def test_function_macro_generated_pragma_is_dispatched_at_invocation():
    handler, seen = _handler({"cpre generated"})
    source = (
        "#define DO_PRAGMA(x) _Pragma(#x)\n"
        "DO_PRAGMA(cpre generated)\n"
        "int kept;\n"
    )

    result = preprocess_source(source, filename="macro.c", pragma_handler=handler)

    assert result.complete
    assert "_Pragma" not in result.source
    assert "int kept;" in result.source
    assert seen == [
        Pragma(
            "cpre generated",
            PragmaOrigin.OPERATOR,
            SourceLocation(2, 1),
            "macro.c",
        )
    ]


def test_object_macro_can_expose_pragma_operator():
    handler, seen = _handler({"cpre object"})
    source = (
        '#define P _Pragma("cpre object")\n'
        "P\n"
        "int kept;\n"
    )

    result = preprocess_source(source, pragma_handler=handler)

    assert result.complete
    assert [pragma.payload for pragma in seen] == ["cpre object"]
    assert seen[0].location == SourceLocation(2, 1)


def test_source_and_operator_pragmas_share_one_ordered_dispatch_path():
    seen = []

    def handler(pragma):
        seen.append((pragma.origin, pragma.payload))
        return PragmaDisposition.CONSUME

    result = preprocess_source(
        '_Pragma("first")\n#pragma second\n_Pragma("third")\n',
        pragma_handler=handler,
    )

    assert result.complete
    assert seen == [
        (PragmaOrigin.OPERATOR, "first"),
        (PragmaOrigin.DIRECTIVE, "second"),
        (PragmaOrigin.OPERATOR, "third"),
    ]


def test_pragma_without_handler_is_atomic_incomplete():
    result = preprocess_source("#pragma unknown\nint hidden;\n")

    assert not result.complete
    assert result.source is None
    assert result.macros is None
    assert result.source_map is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert result.incomplete[0].location == SourceLocation(1, 1)
    assert "caller-provided" in result.incomplete[0].message


def test_handler_can_explicitly_reject_pragma_atomically():
    handler, seen = _handler(set())
    result = preprocess_source('_Pragma("unknown")\n', pragma_handler=handler)

    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert [pragma.payload for pragma in seen] == ["unknown"]


def test_pragmas_in_discarded_branches_do_not_require_handler():
    source = (
        "#if 0\n"
        "#pragma ignored\n"
        '_Pragma("also ignored")\n'
        "#endif\n"
        "int kept;\n"
    )

    result = preprocess_source(source)

    assert result.complete
    assert "int kept;" in result.source


def test_malformed_pragma_operator_is_structured_incomplete_without_dispatch():
    seen = []

    def handler(pragma):
        seen.append(pragma)
        return PragmaDisposition.CONSUME

    result = preprocess_source("_Pragma(not_a_string)\n", pragma_handler=handler)

    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert result.incomplete[0].location == SourceLocation(1, 1)
    assert seen == []


def test_invalid_pragma_handler_result_is_configuration_error():
    with pytest.raises(AnalysisError) as error:
        preprocess_source(
            '#pragma cpre\n',
            pragma_handler=lambda pragma: True,
        )

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


def test_repeated_pragma_preprocessing_is_deterministic():
    source = (
        '#define P _Pragma("stable operator")\n'
        "P\n"
        "#pragma stable directive\n"
        "int kept;\n"
    )

    def run():
        seen = []

        def handler(pragma):
            seen.append(pragma)
            return PragmaDisposition.CONSUME

        return preprocess_source(source, pragma_handler=handler), seen

    first, first_seen = run()
    second, second_seen = run()

    assert first.complete and second.complete
    assert first.source == second.source
    assert first.source_map == second.source_map
    assert first.removed_lines == second.removed_lines
    assert first_seen == second_seen
