from __future__ import annotations

import os

import pytest

import cpre
from cpre import ErrorCode, SourceLocation, preprocess_source
from cpre.cli import main
from cpre.include_queries import IncludeForm
from cpre.includes import IncludeRequest, ResolvedInclude, SearchPathResolver


class MemoryResolver:
    """Resolve headers from a dict keyed by spelling and record every request."""

    def __init__(self, headers):
        self.headers = headers
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        text = self.headers.get(request.header)
        return ResolvedInclude(request.header, text) if text is not None else None


def _kept(result):
    return [line.strip() for line in result.source.splitlines() if line.strip()]


SOURCE = (
    "#if __has_include(<present.h>)\n"
    "int angle;\n"
    "#endif\n"
    '#if __has_include("missing.h")\n'
    "int quoted;\n"
    "#endif\n"
)


def test_resolver_answers_has_include_when_opted_in():
    resolver = MemoryResolver({"present.h": ""})

    result = preprocess_source(
        SOURCE, filename="unit.c", include_resolver=resolver, has_include_from_resolver=True
    )

    assert result.complete
    assert _kept(result) == ["int angle;"]
    assert resolver.requests == [
        IncludeRequest("present.h", IncludeForm.ANGLE, "include", SourceLocation(1, 5), "unit.c"),
        IncludeRequest("missing.h", IncludeForm.QUOTED, "include", SourceLocation(4, 5), "unit.c"),
    ]
    # Probing a header does not include it.
    assert result.includes == ()


def test_resolver_answers_are_opt_in():
    resolver = MemoryResolver({"present.h": ""})

    result = preprocess_source(SOURCE, include_resolver=resolver)

    assert not result.complete
    assert result.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert "caller-provided include availability" in result.incomplete[0].message
    assert resolver.requests == []


@pytest.mark.parametrize("answer", [True, False])
def test_include_query_answer_takes_precedence(answer):
    resolver = MemoryResolver({"present.h": ""})
    source = "#if __has_include(<present.h>)\nint yes;\n#endif\n"

    result = preprocess_source(
        source,
        include_query=lambda query: answer,
        include_resolver=resolver,
        has_include_from_resolver=True,
    )

    assert result.complete
    assert _kept(result) == (["int yes;"] if answer else [])
    assert resolver.requests == []


def test_include_query_none_falls_back_to_resolver():
    resolver = MemoryResolver({"present.h": ""})
    asked = []

    def query(item):
        asked.append(item.header)
        return False if item.header == "other.h" else None

    source = (
        "#if __has_include(<present.h>)\nint present;\n#endif\n"
        "#if __has_include(<other.h>)\nint other;\n#endif\n"
    )
    result = preprocess_source(
        source, include_query=query, include_resolver=resolver, has_include_from_resolver=True
    )

    assert result.complete
    assert _kept(result) == ["int present;"]
    assert asked == ["present.h", "other.h"]
    assert [request.header for request in resolver.requests] == ["present.h"]


def test_resolver_queries_stay_lazy():
    resolver = MemoryResolver({})
    source = (
        "#if 0\n#if __has_include(<a.h>)\n#endif\n#endif\n"
        "#if 1 || __has_include(<b.h>)\nint kept;\n#endif\n"
    )

    result = preprocess_source(source, include_resolver=resolver, has_include_from_resolver=True)

    assert result.complete
    assert _kept(result) == ["int kept;"]
    assert resolver.requests == []


@pytest.mark.parametrize(
    "test", ["#if defined(__has_include)", "#ifdef __has_include", "#if defined __has_include"]
)
def test_feature_test_is_defined_with_resolver_answers(test):
    result = preprocess_source(
        f"{test}\nint yes;\n#endif\n",
        include_resolver=MemoryResolver({}),
        has_include_from_resolver=True,
    )

    assert result.complete
    assert _kept(result) == ["int yes;"]


def test_resolver_answers_queries_in_included_sources_relative_to_their_includer(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "inc" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "main.c").write_text(
        "#include <lib/config.h>\n#if HAVE_SIBLING\nint ok;\n#endif\n"
    )
    (tmp_path / "inc" / "lib" / "config.h").write_text(
        '#if __has_include("sibling.h")\n#define HAVE_SIBLING 1\n#endif\n'
    )
    (tmp_path / "inc" / "lib" / "sibling.h").write_text("")
    main_c = str(tmp_path / "src" / "main.c")
    resolver = SearchPathResolver([tmp_path / "inc"])

    result = preprocess_source(
        (tmp_path / "src" / "main.c").read_text(),
        filename=main_c,
        include_resolver=resolver,
        has_include_from_resolver=True,
    )

    assert result.complete
    assert _kept(result) == ["int ok;"]


def test_resolver_request_for_included_query_names_header_and_depth():
    resolver = MemoryResolver({"config.h": "#if __has_include(<x.h>)\n#endif\n"})

    def query(item):
        return None

    result = preprocess_source(
        '#include "config.h"\n',
        include_query=query,
        include_resolver=resolver,
        has_include_from_resolver=True,
    )

    # The resolver answers what the callback leaves open, so the run completes.
    assert result.complete
    assert [request.header for request in resolver.requests] == ["config.h", "x.h"]
    assert resolver.requests[1].includer == "config.h"
    assert resolver.requests[1].depth == 1


def test_has_include_from_resolver_requires_resolver():
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source("int x;\n", has_include_from_resolver=True)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_has_include_from_resolver_must_be_bool():
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source(
            "int x;\n", include_resolver=MemoryResolver({}), has_include_from_resolver=1
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_resolver_returning_wrong_type_is_rejected():
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source(
            "#if __has_include(<x.h>)\n#endif\n",
            include_resolver=lambda request: "x.h",
            has_include_from_resolver=True,
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_cli_answers_has_include_from_search(tmp_path, capsys):
    _write(
        tmp_path / "t.c",
        "#if __has_include(<opt.h>)\nint yes;\n#endif\n"
        '#if __has_include("absent.h")\nint no;\n#endif\n',
    )
    _write(tmp_path / "inc" / "opt.h", "")
    source = str(tmp_path / "t.c")
    include = str(tmp_path / "inc")

    assert main(["preprocess", "-I", include, source, "--compact"]) == 2
    assert "__has_include requires caller-provided include availability" in (
        capsys.readouterr().err
    )

    assert (
        main(["preprocess", "-I", include, "--has-include-from-search", source, "--compact"]) == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "int yes;\n"
    assert captured.err == ""


def test_cli_quoted_has_include_searches_source_directory(tmp_path, capsys):
    _write(tmp_path / "src" / "t.c", '#if __has_include("local.h")\nint local;\n#endif\n')
    _write(tmp_path / "src" / "local.h", "")
    args = ["preprocess", "--iquote", str(tmp_path / "inc"), "--has-include-from-search"]

    assert main([*args, str(tmp_path / "src" / "t.c"), "--compact"]) == 0
    assert capsys.readouterr().out == "int local;\n"


def test_cli_has_include_from_search_requires_search_paths(tmp_path, capsys):
    _write(tmp_path / "t.c", "int x;\n")

    with pytest.raises(SystemExit) as caught:
        main(["preprocess", "--has-include-from-search", str(tmp_path / "t.c")])

    assert caught.value.code == 2
    assert "--has-include-from-search requires -I or --iquote" in capsys.readouterr().err


def test_cli_has_include_from_search_json_is_complete(tmp_path, capsys):
    _write(tmp_path / "t.c", "#if __has_include(<opt.h>)\nint yes;\n#endif\n")
    include = tmp_path / "inc"
    include.mkdir()

    assert (
        main(
            [
                "preprocess",
                "-I",
                os.fspath(include),
                "--has-include-from-search",
                "--json",
                str(tmp_path / "t.c"),
            ]
        )
        == 0
    )
    assert '"complete": true' in capsys.readouterr().out
