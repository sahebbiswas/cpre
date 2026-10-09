from __future__ import annotations

import os
from pathlib import Path

import pytest

import cpre
from cpre import ErrorCode, SourceLocation, preprocess_source
from cpre.cli import main
from cpre.include_queries import IncludeQuery
from cpre.includes import (
    IncludeForm,
    IncludeOutcome,
    IncludeRequest,
    ResolvedInclude,
    SearchPathResolver,
)


class MemoryResolver:
    """Small in-memory resolver keyed by header spelling."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.requests: list[IncludeRequest] = []

    def __call__(self, request: IncludeRequest) -> ResolvedInclude | None:
        self.requests.append(request)
        text = self.files.get(request.header)
        return None if text is None else ResolvedInclude(request.header, text)


def _compact(result: cpre.PreprocessResult) -> str:
    assert result.complete, result.incomplete
    return cpre.compact(result)


def test_nested_includes_propagate_macro_state_to_later_conditionals():
    resolver = MemoryResolver(
        {
            "config.h": '#define FEATURE 1\n#include "levels.h"\n',
            "levels.h": "#define LEVEL 3\n#undef GONE\n",
        }
    )
    source = (
        "#define GONE\n"
        '#include "config.h"\n'
        "#if FEATURE && LEVEL == 3 && !defined(GONE)\n"
        "int enabled = LEVEL;\n"
        "#else\n"
        "int disabled;\n"
        "#endif\n"
    )
    result = preprocess_source(source, filename="main.c", include_resolver=resolver)
    assert _compact(result) == "int enabled =  3 ;\n"
    assert result.macros is not None
    assert result.macros["FEATURE"].definition is not None
    assert result.macros["GONE"].defined is False
    assert [(item.header, item.includer, item.depth) for item in resolver.requests] == [
        ("config.h", "main.c", 0),
        ("levels.h", "config.h", 1),
    ]
    assert [(record.identity, record.outcome) for record in result.includes] == [
        ("config.h", IncludeOutcome.ENTERED),
        ("levels.h", IncludeOutcome.ENTERED),
    ]


def test_included_output_is_spliced_after_masked_directive_with_provenance():
    resolver = MemoryResolver({"h.h": "int from_header;\n"})
    result = preprocess_source(
        'int before;\n#include "h.h"\nint after;\n',
        filename="main.c",
        include_resolver=resolver,
    )
    assert result.complete
    assert result.source == "int before;\n" + " " * 14 + "\nint from_header;\nint after;\n"
    assert result.removed_lines == frozenset({2})
    assert result.source_map is not None
    identities = [
        (mapping.source_identity, result.source[mapping.output_start : mapping.output_end])
        for mapping in result.source_map
    ]
    assert identities == [
        (None, "int before;\n" + " " * 14 + "\n"),
        ("h.h", "int from_header;\n"),
        (None, "int after;\n"),
    ]
    header_mapping = result.source_map[1]
    assert header_mapping.source_start == 0
    assert header_mapping.start == SourceLocation(1, 1)
    assert result.source_map[2].start == SourceLocation(3, 1)


def test_header_macros_expand_in_primary_source_with_invocation_provenance():
    resolver = MemoryResolver({"h.h": "#define VALUE 42\n"})
    result = preprocess_source(
        '#include "h.h"\nint v = VALUE;\n', filename="main.c", include_resolver=resolver
    )
    assert result.complete
    assert result.source_map is not None
    expanded = [mapping for mapping in result.source_map if mapping.expanded]
    assert len(expanded) == 1
    assert expanded[0].source_identity is None
    assert expanded[0].start == SourceLocation(2, 9)


def test_file_and_line_inside_included_source_use_its_identity():
    resolver = MemoryResolver({"dir/h.h": "\nconst char *f = __FILE__; int l = __LINE__;\n"})
    result = preprocess_source('#include "dir/h.h"\n', filename="main.c", include_resolver=resolver)
    # The header's own blank first line is source text, so compaction keeps it.
    assert _compact(result) == '\nconst char *f =  "dir/h.h" ; int l =  2 ;\n'


def test_include_cycle_terminates_with_structured_diagnostic():
    resolver = MemoryResolver({"a.h": '#include "b.h"\n', "b.h": '\n#include "a.h"\n'})
    result = preprocess_source('#include "a.h"\n', filename="main.c", include_resolver=resolver)
    assert not result.complete
    assert result.source is None
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.INCLUDE_CYCLE
    assert diagnostic.location == SourceLocation(2)
    assert diagnostic.source_identity == "b.h"
    assert "a.h" in diagnostic.message


def test_primary_source_including_itself_is_a_cycle():
    resolver = MemoryResolver({"main.c": "int x;\n"})
    result = preprocess_source('#include "main.c"\n', filename="main.c", include_resolver=resolver)
    assert result.incomplete[0].code is ErrorCode.INCLUDE_CYCLE


def test_guarded_mutual_includes_terminate_without_cycle():
    resolver = MemoryResolver(
        {
            "a.h": '#ifndef A_H\n#define A_H\n#include "b.h"\nint a;\n#endif\n',
            "b.h": '#include "a.h"\nint b;\n',
        }
    )
    result = preprocess_source('#include "a.h"\n', include_resolver=resolver)
    assert _compact(result) == "int b;\nint a;\n"
    assert [(record.identity, record.outcome) for record in result.includes] == [
        ("a.h", IncludeOutcome.ENTERED),
        ("b.h", IncludeOutcome.ENTERED),
        ("a.h", IncludeOutcome.GUARDED),
    ]


def test_include_depth_is_bounded():
    def endless(request: IncludeRequest) -> ResolvedInclude:
        return ResolvedInclude(f"level{request.depth}.h", '#include "next.h"\n')

    result = preprocess_source('#include "next.h"\n', include_resolver=endless, max_include_depth=4)
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.INCLUDE_DEPTH_EXCEEDED
    assert diagnostic.source_identity == "level3.h"
    assert "4" in diagnostic.message

    shallow = MemoryResolver({"one.h": '#include "two.h"\n', "two.h": "int two;\n"})
    assert not preprocess_source(
        '#include "one.h"\n', include_resolver=shallow, max_include_depth=1
    ).complete
    assert preprocess_source(
        '#include "one.h"\n', include_resolver=shallow, max_include_depth=2
    ).complete


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "3"])
def test_max_include_depth_must_be_positive_integer(value):
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source("int x;\n", include_resolver=MemoryResolver({}), max_include_depth=value)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_guarded_header_is_processed_once():
    resolver = MemoryResolver({"g.h": "#ifndef G_H\n#define G_H\nint g;\n#endif\n"})
    result = preprocess_source('#include "g.h"\n#include "g.h"\n', include_resolver=resolver)
    assert _compact(result) == "int g;\n"
    assert [record.outcome for record in result.includes] == [
        IncludeOutcome.ENTERED,
        IncludeOutcome.GUARDED,
    ]


def test_include_guard_reenters_after_undef():
    resolver = MemoryResolver({"g.h": "#ifndef G_H\n#define G_H\nint g;\n#endif\n"})
    result = preprocess_source(
        '#include "g.h"\n#undef G_H\n#include "g.h"\n', include_resolver=resolver
    )
    assert _compact(result) == "int g;\nint g;\n"


def test_configured_guard_macro_is_respected():
    resolver = MemoryResolver({"g.h": "#ifndef G_H\n#define G_H\nint g;\n#endif\n"})
    result = preprocess_source(
        '#include "g.h"\nint x;\n',
        configuration=cpre.MacroConfiguration(presence=["G_H"]),
        include_resolver=resolver,
    )
    assert _compact(result) == "int x;\n"
    assert result.includes[0].outcome is IncludeOutcome.GUARDED


def test_pragma_once_header_is_processed_once():
    resolver = MemoryResolver({"once.h": "#pragma once\nint once;\n"})
    result = preprocess_source('#include "once.h"\n#include <once.h>\n', include_resolver=resolver)
    assert _compact(result) == "int once;\n"
    assert [record.outcome for record in result.includes] == [
        IncludeOutcome.ENTERED,
        IncludeOutcome.PRAGMA_ONCE,
    ]


def test_pragma_once_in_inactive_branch_does_not_apply():
    resolver = MemoryResolver({"h.h": "#if 0\n#pragma once\n#endif\nint h;\n"})
    result = preprocess_source('#include "h.h"\n#include "h.h"\n', include_resolver=resolver)
    assert _compact(result) == "int h;\nint h;\n"


def test_unguarded_header_is_processed_each_time():
    resolver = MemoryResolver({"plain.h": "int plain;\n"})
    result = preprocess_source(
        '#include "plain.h"\n#include "plain.h"\n', include_resolver=resolver
    )
    assert _compact(result) == "int plain;\nint plain;\n"


def test_import_includes_once():
    resolver = MemoryResolver({"i.h": "int i;\n"})
    result = preprocess_source(
        '#import "i.h"\n#import "i.h"\n#include "i.h"\n', include_resolver=resolver
    )
    assert _compact(result) == "int i;\n"
    assert [record.outcome for record in result.includes] == [
        IncludeOutcome.ENTERED,
        IncludeOutcome.IMPORTED,
        IncludeOutcome.IMPORTED,
    ]
    assert resolver.requests[0].directive == "import"


def test_include_next_request_carries_directive_and_includer():
    resolver = MemoryResolver({"wrap.h": "#include_next <wrap.h>\n", "missing": ""})

    def resolve(request: IncludeRequest) -> ResolvedInclude | None:
        resolver.requests.append(request)
        if request.directive == "include_next":
            return ResolvedInclude("system/wrap.h", "int real;\n")
        return ResolvedInclude("local/wrap.h", "#include_next <wrap.h>\n")

    result = preprocess_source("#include <wrap.h>\n", filename="m.c", include_resolver=resolve)
    assert _compact(result) == "int real;\n"
    assert [
        (request.directive, request.form, request.includer) for request in resolver.requests
    ] == [
        ("include", IncludeForm.ANGLE, "m.c"),
        ("include_next", IncludeForm.ANGLE, "local/wrap.h"),
    ]


def test_unresolved_include_is_a_structured_diagnostic():
    resolver = MemoryResolver({"h.h": "#include <missing.h>\n"})
    result = preprocess_source('#include "h.h"\n', include_resolver=resolver)
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNRESOLVED_INCLUDE
    assert diagnostic.message == "cannot resolve #include <missing.h>"
    assert diagnostic.location == SourceLocation(1)
    assert diagnostic.source_identity == "h.h"


def test_skip_includes_falls_back_for_unresolved_headers_only():
    resolver = MemoryResolver({"h.h": "#include <system.h>\n#define LOCAL 1\n"})
    result = preprocess_source(
        '#include "h.h"\n#if LOCAL\nint local;\n#endif\n',
        include_resolver=resolver,
        skip_includes=True,
    )
    assert _compact(result) == "int local;\n"
    assert result.skipped_includes == (
        cpre.SkippedInclude("include", "<system.h>", SourceLocation(1), "h.h"),
    )
    assert [record.identity for record in result.includes] == ["h.h"]


def test_closed_skip_fallback_keeps_names_unknown_but_enters_guarded_headers():
    resolver = MemoryResolver({"g.h": "#ifndef G_H\n#define G_H\n#define LOCAL 1\n#endif\n"})
    closed = cpre.MacroConfiguration(unknown_names="undefined")
    result = preprocess_source(
        '#include <system.h>\n#include "g.h"\n#include "g.h"\n#if LOCAL\nint local;\n#endif\n',
        configuration=closed,
        include_resolver=resolver,
        skip_includes=True,
    )
    assert _compact(result) == "int local;\n"
    assert [record.outcome for record in result.includes] == [
        IncludeOutcome.ENTERED,
        IncludeOutcome.GUARDED,
    ]

    unknown = preprocess_source(
        "#include <system.h>\n#ifdef FROM_SYSTEM\nint on;\n#endif\n",
        configuration=closed,
        include_resolver=resolver,
        skip_includes=True,
    )
    assert unknown.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION


def test_computed_include_is_macro_expanded():
    resolver = MemoryResolver({"a/b.h": "int b;\n", "c.h": "int c;\n"})
    result = preprocess_source(
        '#define ANGLE <a/b.h>\n#define QUOTED "c.h"\n#include ANGLE\n#include QUOTED\n',
        include_resolver=resolver,
    )
    assert _compact(result) == "int b;\nint c;\n"
    assert [(request.header, request.form) for request in resolver.requests] == [
        ("a/b.h", IncludeForm.ANGLE),
        ("c.h", IncludeForm.QUOTED),
    ]


def test_direct_header_names_are_not_lexed_as_tokens():
    seen: list[str] = []

    def resolve(request: IncludeRequest) -> ResolvedInclude:
        seen.append(request.header)
        return ResolvedInclude(request.header, "")

    result = preprocess_source(
        '#include <a//b.h> // comment\n#include "x\\\\y.h" /* c */\n#inc\\\nlude <z.h>\n',
        include_resolver=resolve,
    )
    assert result.complete
    assert seen == ["a//b.h", "x\\\\y.h", "z.h"]


@pytest.mark.parametrize(
    ("directive", "message"),
    [
        ("#include", "#include expects a header name"),
        ('#include "a.h" extra', "unexpected tokens after the include header name"),
        ("#include <>", "empty include header name"),
        ("#include 42", 'include operand must be "header" or <header>, or macro-expand to one'),
    ],
)
def test_malformed_include_operands_are_diagnosed(directive, message):
    result = preprocess_source(directive + "\n", include_resolver=MemoryResolver({}))
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert diagnostic.message == message


def test_inactive_includes_are_not_resolved():
    resolver = MemoryResolver({})
    result = preprocess_source(
        '#if 0\n#include "never.h"\n#endif\nint x;\n', include_resolver=resolver
    )
    assert result.complete
    assert resolver.requests == []


def test_resolver_contract_is_validated():
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source("int x;\n", include_resolver="not callable")
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source('#include "a.h"\n', include_resolver=lambda request: "int a;\n")
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    with pytest.raises(cpre.AnalysisError):
        ResolvedInclude("", "int x;\n")


def test_malformed_conditional_in_header_raises_with_header_identity():
    resolver = MemoryResolver({"bad.h": "#if 1\n"})
    with pytest.raises(cpre.ParseError) as caught:
        preprocess_source('#include "bad.h"\n', filename="main.c", include_resolver=resolver)
    assert caught.value.code is ErrorCode.UNTERMINATED_CONDITIONAL
    assert caught.value.filename == "bad.h"


def test_unresolved_condition_in_header_reports_header_identity():
    resolver = MemoryResolver({"h.h": "\n#if UNKNOWN\n#endif\n"})
    result = preprocess_source('#include "h.h"\n', include_resolver=resolver)
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNRESOLVED_CONDITION
    assert diagnostic.location == SourceLocation(2)
    assert diagnostic.source_identity == "h.h"


def _recording_query(answers: dict[tuple[IncludeForm, str], bool | None]):
    seen: list[IncludeQuery] = []

    def query(item: IncludeQuery) -> bool | None:
        seen.append(item)
        return answers.get((item.form, item.header))

    return query, seen


def test_has_include_inside_included_source_is_answered_with_header_identity():
    resolver = MemoryResolver(
        {
            "config.h": (
                "#if __has_include(<optional.h>)\n"
                "#define HAVE_OPTIONAL 1\n"
                "#endif\n"
                '#if __has_include("local.h")\n'
                "#define HAVE_LOCAL 1\n"
                "#endif\n"
            )
        }
    )
    query, seen = _recording_query(
        {(IncludeForm.ANGLE, "optional.h"): True, (IncludeForm.QUOTED, "local.h"): False}
    )
    result = preprocess_source(
        '#include "config.h"\n#if HAVE_OPTIONAL && !defined(HAVE_LOCAL)\nint yes;\n#endif\n',
        filename="main.c",
        configuration=cpre.MacroConfiguration(unknown_names="undefined"),
        include_resolver=resolver,
        include_query=query,
    )
    assert _compact(result) == "int yes;\n"
    assert seen == [
        IncludeQuery("optional.h", IncludeForm.ANGLE, SourceLocation(1, 5), "config.h"),
        IncludeQuery("local.h", IncludeForm.QUOTED, SourceLocation(4, 5), "config.h"),
    ]


def test_has_include_in_header_uses_live_macro_state_per_entry():
    resolver = MemoryResolver(
        {"probe.h": "#if __has_include(HEADER)\nint found;\n#else\nint missing;\n#endif\n"}
    )
    query, seen = _recording_query(
        {(IncludeForm.QUOTED, "a.h"): True, (IncludeForm.QUOTED, "b.h"): False}
    )
    result = preprocess_source(
        '#define HEADER "a.h"\n#include "probe.h"\n'
        '#undef HEADER\n#define HEADER "b.h"\n#include "probe.h"\n',
        include_resolver=resolver,
        include_query=query,
    )
    assert _compact(result) == "int found;\nint missing;\n"
    assert [item.header for item in seen] == ["a.h", "b.h"]


def test_unreachable_has_include_in_header_is_not_queried():
    resolver = MemoryResolver(
        {
            "h.h": (
                "#if 1 || __has_include(<short.h>)\nint a;\n#endif\n"
                "#ifdef OFF\n#if __has_include(<dead.h>)\n#endif\n#endif\n"
                "#if 0\n#elif __has_include(<live.h>)\nint b;\n#endif\n"
            )
        }
    )
    query, seen = _recording_query({(IncludeForm.ANGLE, "live.h"): True})
    result = preprocess_source(
        '#undef OFF\n#include "h.h"\n', include_resolver=resolver, include_query=query
    )
    assert _compact(result) == "int a;\nint b;\n"
    assert [item.header for item in seen] == ["live.h"]


@pytest.mark.parametrize(
    ("include_query", "message"),
    [
        (None, "__has_include requires caller-provided include availability"),
        (lambda query: None, "include availability is unknown for <x.h>"),
    ],
)
def test_unanswered_has_include_in_header_is_atomic_incomplete(include_query, message):
    resolver = MemoryResolver({"h.h": "int h;\n#if __has_include(<x.h>)\n#endif\n"})
    result = preprocess_source(
        '#include "h.h"\nint main;\n', include_resolver=resolver, include_query=include_query
    )
    assert result.source is None
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNRESOLVED_CONDITION
    assert diagnostic.message == message
    assert diagnostic.location == SourceLocation(2, 5)
    assert diagnostic.source_identity == "h.h"


def test_has_include_in_primary_source_sees_header_macros():
    resolver = MemoryResolver({"h.h": "#define OPTIONAL <optional.h>\n#define ON 1\n"})
    queries: list[str] = []

    def has_include(query: object) -> bool:
        queries.append(query.header)  # type: ignore[attr-defined]
        return True

    result = preprocess_source(
        '#include "h.h"\n#if ON && __has_include(OPTIONAL)\nint yes;\n#endif\n',
        include_resolver=resolver,
        include_query=has_include,
    )
    assert _compact(result) == "int yes;\n"
    assert queries == ["optional.h"]


def test_header_pragmas_are_dispatched_with_header_identity_and_masked():
    resolver = MemoryResolver({"p.h": "#pragma pack(1)\nint p;\n"})
    seen: list[cpre.Pragma] = []

    def handle(pragma: cpre.Pragma) -> cpre.PragmaDisposition:
        seen.append(pragma)
        return cpre.PragmaDisposition.CONSUME

    result = preprocess_source(
        '#include "p.h"\n#pragma main\n', include_resolver=resolver, pragma_handler=handle
    )
    assert [(pragma.payload, pragma.filename, pragma.location) for pragma in seen] == [
        ("pack(1)", "p.h", SourceLocation(1, 1)),
        ("main", None, SourceLocation(2, 1)),
    ]
    assert result.source is not None
    assert "#" not in result.source and "0;" not in result.source
    assert result.removed_lines == frozenset({1, 2, 4})
    assert cpre.compact(result) == "int p;\n"


def test_unhandled_header_pragma_is_incomplete_with_header_identity():
    resolver = MemoryResolver({"p.h": "int p;\n#pragma vendor thing\n"})
    result = preprocess_source('#include "p.h"\n', include_resolver=resolver)
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert diagnostic.location == SourceLocation(2)
    assert diagnostic.source_identity == "p.h"


def test_pragma_operator_in_header_reports_header_location():
    resolver = MemoryResolver({"op.h": 'int a;\nint b; _Pragma("x") int c;\n'})
    seen: list[cpre.Pragma] = []

    def handle(pragma: cpre.Pragma) -> cpre.PragmaDisposition:
        seen.append(pragma)
        return cpre.PragmaDisposition.CONSUME

    result = preprocess_source(
        'int m;\n#include "op.h"\n', include_resolver=resolver, pragma_handler=handle
    )
    assert result.complete
    assert [(pragma.payload, pragma.filename, pragma.location) for pragma in seen] == [
        ("x", "op.h", SourceLocation(2, 8)),
    ]


def test_missing_final_newlines_do_not_join_lines():
    resolver = MemoryResolver({"e.h": "int e;"})
    result = preprocess_source('int a;\n#include "e.h"', include_resolver=resolver)
    assert result.complete
    assert result.source == "int a;\n" + " " * 14 + "\nint e;\n"
    assert result.removed_lines == frozenset({2})
    assert result.source_map is not None
    synthetic = [
        mapping for mapping in result.source_map if mapping.source_start == mapping.source_end
    ]
    assert [(mapping.source_identity, mapping.expanded) for mapping in synthetic] == [
        (None, True),
        ("e.h", True),
    ]

    joined = preprocess_source('#include "e.h"\nint after;\n', include_resolver=resolver)
    assert _compact(joined) == "int e;\nint after;\n"


def test_line_endings_are_preserved_per_source():
    resolver = MemoryResolver({"w.h": "int w;\r\n"})
    result = preprocess_source('#include "w.h"\r\nint m;\r\n', include_resolver=resolver)
    assert result.source == " " * 14 + "\r\nint w;\r\nint m;\r\n"


def test_no_resolver_behavior_is_unchanged():
    source = '#include "x.h"\nint after;\n'
    result = preprocess_source(source)
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert result.incomplete[0].message == "include processing is not supported"
    assert result.incomplete[0].source_identity is None

    skipped = preprocess_source(source, skip_includes=True)
    assert skipped.complete
    assert skipped.includes == ()
    assert skipped.source_map is not None
    assert all(mapping.source_identity is None for mapping in skipped.source_map)


def test_search_path_resolver_order_and_include_next(tmp_path: Path):
    for directory, name, text in [
        ("src", "main.c", ""),
        ("src", "local.h", "local\n"),
        ("quote", "local.h", "quote\n"),
        ("quote", "q.h", "q\n"),
        ("one", "q.h", "one-q\n"),
        ("one", "std.h", "one-std\n"),
        ("two", "std.h", "two-std\n"),
    ]:
        (tmp_path / directory).mkdir(exist_ok=True)
        (tmp_path / directory / name).write_bytes(text.encode("utf-8"))
    resolver = SearchPathResolver(
        [tmp_path / "one", tmp_path / "two"], quote_paths=[tmp_path / "quote"]
    )
    main_path = os.path.join(tmp_path, "src", "main.c")

    def request(header, form=IncludeForm.QUOTED, directive="include", includer=main_path):
        return resolver(IncludeRequest(header, form, directive, SourceLocation(1), includer))

    local = request("local.h")
    assert local is not None
    assert local.source == "local\n"
    assert local.identity == os.path.normpath(os.path.join(tmp_path, "src", "local.h"))
    assert request("q.h").source == "q\n"
    assert request("q.h", IncludeForm.ANGLE).source == "one-q\n"
    assert request("local.h", IncludeForm.ANGLE) is None
    first = request("std.h", IncludeForm.ANGLE)
    assert first.source == "one-std\n"
    following = request("std.h", IncludeForm.ANGLE, "include_next", first.identity)
    assert following.source == "two-std\n"
    assert request("std.h", IncludeForm.ANGLE, "include_next", following.identity) is None
    absolute = os.path.join(tmp_path, "two", "std.h")
    assert request(absolute, IncludeForm.ANGLE).source == "two-std\n"


def test_search_path_resolver_preserves_line_endings_and_reports_read_errors(tmp_path: Path):
    (tmp_path / "crlf.h").write_bytes(b"int a;\r\n")
    (tmp_path / "bad.h").write_bytes(b"\xff\xfe")
    resolver = SearchPathResolver([tmp_path])
    resolved = resolver(IncludeRequest("crlf.h", IncludeForm.ANGLE, "include", SourceLocation(1)))
    assert resolved is not None and resolved.source == "int a;\r\n"
    with pytest.raises(cpre.AnalysisError) as caught:
        resolver(IncludeRequest("bad.h", IncludeForm.ANGLE, "include", SourceLocation(1)))
    assert caught.value.code is ErrorCode.SOURCE_READ_ERROR


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_cli_resolves_includes_with_search_paths(tmp_path, capsys):
    _write(
        tmp_path / "src" / "main.c",
        '#include "local.h"\n#include <lib.h>\n#if LOCAL && LIB == 2\nint ok;\n#endif\n',
    )
    _write(tmp_path / "src" / "local.h", "#define LOCAL 1\n")
    _write(tmp_path / "inc" / "lib.h", "#pragma once\n#define LIB 2\n")
    main_c = str(tmp_path / "src" / "main.c")

    assert main(["preprocess", main_c, "-I", str(tmp_path / "inc"), "--compact"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "int ok;\n"
    assert captured.err == ""

    assert main(["preprocess", main_c, "--compact"]) == 2
    assert "include processing is not supported" in capsys.readouterr().err

    assert main(["preprocess", main_c, "--iquote", str(tmp_path / "inc")]) == 2
    assert "cannot resolve #include <lib.h>" in capsys.readouterr().err


def test_cli_reports_header_diagnostics_against_header_path(tmp_path, capsys):
    _write(tmp_path / "main.c", '#include "h.h"\n')
    _write(tmp_path / "inc" / "h.h", "\n#include <missing.h>\n")
    header = os.path.normpath(tmp_path / "inc" / "h.h")
    assert main(["preprocess", str(tmp_path / "main.c"), "-I", str(tmp_path / "inc")]) == 2
    assert capsys.readouterr().err == f"{header}: line 2: cannot resolve #include <missing.h>\n"

    assert (
        main(
            [
                "preprocess",
                str(tmp_path / "main.c"),
                "-I",
                str(tmp_path / "inc"),
                "--skip-includes",
                "--compact",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "\n"
    assert captured.err == f"{header}: line 2: skipped #include <missing.h>\n"


def test_cli_max_include_depth(tmp_path, capsys):
    _write(tmp_path / "main.c", '#include "a.h"\n')
    _write(tmp_path / "a.h", '#include "b.h"\n')
    _write(tmp_path / "b.h", "int b;\n")
    main_c = str(tmp_path / "main.c")
    assert main(["preprocess", main_c, "-I", str(tmp_path), "--max-include-depth", "1"]) == 2
    assert "exceeds the maximum include depth of 1" in capsys.readouterr().err
    assert main(["preprocess", main_c, "-I", str(tmp_path), "--max-include-depth", "0"]) == 2
    assert "max_include_depth must be a positive integer" in capsys.readouterr().err


def test_resource_limit_inside_header_reports_header_identity():
    resolver = MemoryResolver({"h.h": "\n#if A && B && C\n#endif\n"})
    result = preprocess_source(
        '#include "h.h"\n',
        include_resolver=resolver,
        options=cpre.AnalysisOptions(max_atoms=2),
    )
    (diagnostic,) = result.incomplete
    assert isinstance(diagnostic, cpre.AnalysisIncomplete)
    assert diagnostic.code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert diagnostic.source_identity == "h.h"


def test_lower_level_entry_points_keep_header_pragmas_unsupported():
    from cpre.include_queries import preprocess_source as include_query_preprocess

    resolver = MemoryResolver({"p.h": "#pragma pack(1)\nint p;\n"})
    result = include_query_preprocess('#include "p.h"\n', include_resolver=resolver)
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert diagnostic.source_identity == "p.h"


def test_has_include_prefix_rerun_tolerates_header_pragmas():
    resolver = MemoryResolver({"h.h": "#pragma pack(1)\n#define ON 1\n"})
    result = preprocess_source(
        '#include "h.h"\n#if ON && __has_include(<x.h>)\nint yes;\n#endif\n',
        include_resolver=resolver,
        include_query=lambda query: True,
        pragma_handler=lambda pragma: cpre.PragmaDisposition.CONSUME,
    )
    assert _compact(result) == "int yes;\n"
