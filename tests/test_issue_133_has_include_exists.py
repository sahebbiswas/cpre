"""``__has_include`` probes ask a resolver whether a header exists without reading it (#133)."""

from __future__ import annotations

import pytest

from cpre import AnalysisError, ErrorCode, preprocess_source
from cpre.cli import main
from cpre.includes import (
    ExistenceIncludeResolver,
    IncludeForm,
    IncludeRequest,
    ResolvedInclude,
    SearchPathResolver,
)

SOURCE = "#if __has_include(<latin1.h>)\nint yes;\n#endif\n"


def _undecodable_tree(tmp_path):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "latin1.h").write_bytes(b"\xe9\n")
    return inc


def _probe(resolver, source=SOURCE):
    return preprocess_source(
        source, filename="t.c", include_resolver=resolver, has_include_from_resolver=True
    )


def test_probe_of_an_undecodable_header_answers_available(tmp_path):
    result = _probe(SearchPathResolver([_undecodable_tree(tmp_path)]))
    assert result.complete
    assert "int yes;" in result.source


def test_include_of_the_same_header_still_reports_the_read_error(tmp_path):
    resolver = SearchPathResolver([_undecodable_tree(tmp_path)])
    with pytest.raises(AnalysisError) as raised:
        preprocess_source("#include <latin1.h>\n", filename="t.c", include_resolver=resolver)
    assert raised.value.code is ErrorCode.SOURCE_READ_ERROR


def test_probe_of_a_missing_header_answers_unavailable(tmp_path):
    inc = tmp_path / "inc"
    inc.mkdir()
    result = _probe(SearchPathResolver([inc]))
    assert result.complete
    assert "int yes;" not in result.source


def test_probe_does_not_read_the_header(tmp_path, monkeypatch):
    inc = _undecodable_tree(tmp_path)
    opened = []
    real_open = open

    def tracking_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    assert _probe(SearchPathResolver([inc])).complete
    assert not any(str(path).endswith("latin1.h") for path in opened)


def test_exists_matches_the_request_semantics(tmp_path):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "a.h").write_text("")
    (tmp_path / "local.h").write_text("")
    resolver = SearchPathResolver([inc])
    location = None

    def request(header, form, includer=None):
        return IncludeRequest(header, form, "include", location, includer, 0)

    assert resolver.exists(request("a.h", IncludeForm.ANGLE))
    assert not resolver.exists(request("b.h", IncludeForm.ANGLE))
    # Quoted forms search the includer's directory; angle forms do not.
    includer = str(tmp_path / "main.c")
    assert resolver.exists(request("local.h", IncludeForm.QUOTED, includer))
    assert not resolver.exists(request("local.h", IncludeForm.ANGLE, includer))
    assert isinstance(resolver, ExistenceIncludeResolver)


def test_custom_resolver_without_exists_keeps_calling_the_resolver():
    requests = []

    def resolver(request):
        requests.append(request)
        return ResolvedInclude("latin1.h", "") if request.header == "latin1.h" else None

    assert not isinstance(resolver, ExistenceIncludeResolver)
    result = _probe(resolver)
    assert result.complete and "int yes;" in result.source
    assert [item.header for item in requests] == ["latin1.h"]


def test_custom_exists_is_used_for_probes_and_must_return_a_bool():
    class Resolver:
        def __init__(self, answer):
            self.answer = answer
            self.calls = []

        def __call__(self, request):  # pragma: no cover - a probe must not call this
            raise AssertionError("a probe must use exists()")

        def exists(self, request):
            self.calls.append(request.header)
            return self.answer

    resolver = Resolver(True)
    assert "int yes;" in _probe(resolver).source
    assert resolver.calls == ["latin1.h"]

    with pytest.raises(AnalysisError) as raised:
        _probe(Resolver("yes"))
    assert raised.value.code is ErrorCode.INVALID_CONFIGURATION


def test_cli_probe_of_an_undecodable_header_answers_available(tmp_path, capsys):
    inc = _undecodable_tree(tmp_path)
    target = tmp_path / "t.c"
    target.write_text(SOURCE)
    argv = ["preprocess", "-I", str(inc), "--has-include-from-search", str(target)]
    assert main(argv) == 0
    assert "int yes;" in capsys.readouterr().out
