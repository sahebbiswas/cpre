from cpre import ErrorCode, SourceLocation, preprocess_source
from cpre.include_queries import IncludeForm, IncludeQuery


def _provider(answers):
    seen = []

    def query(item):
        seen.append(item)
        return answers.get((item.form, item.header))

    return query, seen


def test_has_include_distinguishes_quoted_and_angle_forms():
    source = (
        '#if __has_include(<optional.h>)\n'
        'int angle;\n'
        '#endif\n'
        '#if __has_include("local/config.h")\n'
        'int quoted;\n'
        '#endif\n'
    )
    provider, seen = _provider({
        (IncludeForm.ANGLE, "optional.h"): True,
        (IncludeForm.QUOTED, "local/config.h"): True,
    })

    result = preprocess_source(source, filename="unit.c", include_query=provider)

    assert result.complete
    assert "int angle;" in result.source
    assert "int quoted;" in result.source
    assert seen == [
        IncludeQuery("optional.h", IncludeForm.ANGLE, SourceLocation(1, 5), "unit.c"),
        IncludeQuery("local/config.h", IncludeForm.QUOTED, SourceLocation(4, 5), "unit.c"),
    ]


def test_has_include_false_selects_else_branch():
    provider, _ = _provider({(IncludeForm.ANGLE, "optional.h"): False})
    result = preprocess_source(
        "#if __has_include(<optional.h>)\nint yes;\n#else\nint no;\n#endif\n",
        include_query=provider,
    )

    assert result.complete
    assert "int yes;" not in result.source
    assert "int no;" in result.source


def test_has_include_unknown_is_atomic_incomplete():
    provider, seen = _provider({})
    result = preprocess_source(
        "#if __has_include(<optional.h>)\nint yes;\n#endif\n",
        filename="unknown.c",
        include_query=provider,
    )

    assert not result.complete
    assert result.source is None
    assert result.macros is None
    assert result.source_map is None
    assert len(result.incomplete) == 1
    assert result.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert result.incomplete[0].location == SourceLocation(1, 5)
    assert seen[0].header == "optional.h"


def test_has_include_without_provider_is_incomplete_not_false():
    result = preprocess_source(
        "#if __has_include(<optional.h>)\nint yes;\n#endif\n"
    )

    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert "caller-provided" in result.incomplete[0].message


def test_has_include_macro_expands_header_operand_from_active_state():
    source = (
        "#define HEADER <optional.h>\n"
        "#if __has_include(HEADER)\n"
        "int yes;\n"
        "#endif\n"
    )
    provider, seen = _provider({(IncludeForm.ANGLE, "optional.h"): True})

    result = preprocess_source(source, include_query=provider)

    assert result.complete
    assert "int yes;" in result.source
    assert [query.header for query in seen] == ["optional.h"]


def test_has_include_uses_source_order_macro_state():
    source = (
        '#define HEADER "first.h"\n'
        "#if __has_include(HEADER)\n"
        "int first;\n"
        "#endif\n"
        "#undef HEADER\n"
        '#define HEADER "second.h"\n'
        "#if __has_include(HEADER)\n"
        "int second;\n"
        "#endif\n"
    )
    provider, seen = _provider({
        (IncludeForm.QUOTED, "first.h"): True,
        (IncludeForm.QUOTED, "second.h"): True,
    })

    result = preprocess_source(source, include_query=provider)

    assert result.complete
    assert [query.header for query in seen] == ["first.h", "second.h"]


def test_has_include_works_in_elif():
    provider, seen = _provider({(IncludeForm.QUOTED, "fallback.h"): True})
    result = preprocess_source(
        '#if 0\nint no;\n#elif __has_include("fallback.h")\nint yes;\n#endif\n',
        include_query=provider,
    )

    assert result.complete
    assert "int yes;" in result.source
    assert len(seen) == 1


def test_unreachable_has_include_is_not_queried():
    calls = []

    def provider(query):
        calls.append(query)
        return None

    result = preprocess_source(
        "#if 1\nint yes;\n#elif __has_include(<never.h>)\nint no;\n#endif\n",
        include_query=provider,
    )

    assert result.complete
    assert calls == []


def test_boolean_short_circuit_does_not_force_has_include_query():
    calls = []

    def provider(query):
        calls.append(query)
        return None

    result = preprocess_source(
        "#if 1 || __has_include(<never.h>)\nint yes;\n#endif\n",
        include_query=provider,
    )

    assert result.complete
    assert calls == []


def test_nested_selection_only_queries_reachable_has_include():
    calls = []

    def provider(query):
        calls.append(query.header)
        return query.header == "live.h"

    source = (
        "#if 0\n"
        "#if __has_include(<dead.h>)\nint dead;\n#endif\n"
        "#else\n"
        "#if __has_include(<live.h>)\nint live;\n#endif\n"
        "#endif\n"
    )
    result = preprocess_source(source, include_query=provider)

    assert result.complete
    assert calls == ["live.h"]
    assert "int live;" in result.source


def test_repeated_has_include_preprocessing_is_deterministic():
    provider, _ = _provider({(IncludeForm.ANGLE, "stable.h"): True})
    source = "#if __has_include(<stable.h>)\nint stable;\n#endif\n"

    first = preprocess_source(source, include_query=provider)
    second = preprocess_source(source, include_query=provider)

    assert first.source == second.source
    assert first.source_map == second.source_map
    assert first.removed_lines == second.removed_lines


def test_malformed_has_include_operand_is_structured_incomplete():
    calls = []

    def provider(query):
        calls.append(query)
        return True

    result = preprocess_source(
        "#if __has_include(NOT_A_HEADER + 1)\nint no;\n#endif\n",
        include_query=provider,
    )

    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION
    assert result.incomplete[0].location == SourceLocation(1, 5)
    assert calls == []


def test_malformed_unclosed_has_include_is_structured_incomplete():
    result = preprocess_source(
        "#if __has_include(<broken.h>\nint no;\n#endif\n",
        include_query=lambda query: True,
    )

    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_CONDITION_EXPRESSION


def test_has_include_offsets_follow_parser_splitlines_semantics():
    provider, seen = _provider({(IncludeForm.ANGLE, "optional.h"): True})
    source = "int before;\f#if __has_include(<optional.h>)\nint yes;\n#endif\n"

    result = preprocess_source(source, include_query=provider)

    assert result.complete
    assert "int before;" in result.source
    assert "int yes;" in result.source
    assert seen == [
        IncludeQuery("optional.h", IncludeForm.ANGLE, SourceLocation(2, 5), None)
    ]
