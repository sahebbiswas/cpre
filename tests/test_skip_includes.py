from __future__ import annotations

from pathlib import Path

import pytest

import cpre
from cpre import ErrorCode, SkippedInclude, SourceLocation, preprocess_source
from cpre.cli import main


@pytest.mark.parametrize(
    ("directive", "kind", "operand"),
    [
        ('#include "local.h"', "include", '"local.h"'),
        ("#include <system.h>", "include", "<system.h>"),
        ("#include_next <system.h>", "include_next", "<system.h>"),
        ('#import "framework.h"', "import", '"framework.h"'),
        ("#  include   <spaced.h>  /* note */", "include", "<spaced.h>"),
    ],
)
def test_skip_mode_masks_include_and_completes(directive, kind, operand):
    source = f"int before;\n{directive}\nint after;\n"
    result = preprocess_source(source, skip_includes=True, filename="x.c")
    assert result.complete
    assert result.source == "int before;\n" + " " * len(directive) + "\nint after;\n"
    assert result.skipped_includes == (SkippedInclude(kind, operand, SourceLocation(2)),)
    assert result.removed_lines == frozenset({2})


@pytest.mark.parametrize("directive", ['#include "x.h"', "#include_next <x.h>", '#import "x.h"'])
def test_default_still_rejects_includes(directive):
    source = directive + "\nint after;\n"
    default = preprocess_source(source)
    explicit = preprocess_source(source, skip_includes=False)
    assert default == explicit
    assert not default.complete
    assert default.source is None
    assert default.skipped_includes == ()
    assert default.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert default.incomplete[0].message == "include processing is not supported"


def test_skip_mode_preserves_line_endings_exactly():
    source = '#include "a.h"\r\n#include <b.h>\r#include \\\r\n  "c.h"\nint x;'
    result = preprocess_source(source, skip_includes=True)
    assert result.complete
    assert result.source is not None
    assert len(result.source) == len(source)
    assert [line[len(line.rstrip("\r\n")) :] for line in result.source.splitlines(True)] == [
        line[len(line.rstrip("\r\n")) :] for line in source.splitlines(True)
    ]
    assert result.source.endswith("int x;")
    assert [item.location.line for item in result.skipped_includes] == [1, 2, 3]
    assert result.skipped_includes[2].operand == '"c.h"'


def test_skip_mode_handles_directive_name_split_by_line_splice():
    result = preprocess_source("#inc\\\nlude <d.h>\nint x;\n", skip_includes=True)
    assert result.complete
    assert result.source == "     \n          \nint x;\n"
    assert result.skipped_includes == (SkippedInclude("include", "<d.h>", SourceLocation(1)),)


def test_skip_mode_reports_each_include_independently():
    source = '#include "a.h"\n#include "a.h"\n#include <b.h>\n#import "c.h"\n'
    result = preprocess_source(source, skip_includes=True)
    assert result.complete
    assert result.skipped_includes == (
        SkippedInclude("include", '"a.h"', SourceLocation(1)),
        SkippedInclude("include", '"a.h"', SourceLocation(2)),
        SkippedInclude("include", "<b.h>", SourceLocation(3)),
        SkippedInclude("import", '"c.h"', SourceLocation(4)),
    )


def test_skip_mode_does_not_fabricate_header_macros():
    source = '#include "config.h"\n#ifdef FROM_HEADER\nint yes;\n#endif\nFROM_HEADER\n'
    unresolved = preprocess_source(source, skip_includes=True)
    assert not unresolved.complete
    assert unresolved.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert unresolved.incomplete[0].location == SourceLocation(2)

    without_condition = preprocess_source(
        '#include "config.h"\nFROM_HEADER value;\n', skip_includes=True
    )
    assert without_condition.complete
    assert without_condition.macros == {}
    assert without_condition.source == " " * 19 + "\nFROM_HEADER value;\n"


CLOSED = cpre.MacroConfiguration(unknown_names="undefined")


def test_closed_skip_mode_keeps_header_suppliable_names_unknown():
    source = '#include "config.h"\n#ifdef FEATURE\nint on;\n#endif\n'
    result = preprocess_source(source, configuration=CLOSED, skip_includes=True)
    assert not result.complete
    (diagnostic,) = result.incomplete
    assert diagnostic.code is ErrorCode.UNRESOLVED_CONDITION
    assert diagnostic.location == SourceLocation(2)
    assert [macro.name for macro in diagnostic.unresolved] == ["FEATURE"]

    value_use = preprocess_source(
        '#include "config.h"\n#if FEATURE > 1\nint on;\n#endif\n',
        configuration=CLOSED,
        skip_includes=True,
    )
    assert value_use.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION


@pytest.mark.parametrize(
    ("configuration", "selected"),
    [
        (cpre.MacroConfiguration(unknown_names="undefined", undefined={"FEATURE"}), False),
        (
            cpre.MacroConfiguration(
                unknown_names="undefined", definitions=[cpre.MacroDefinition("FEATURE", "1")]
            ),
            True,
        ),
    ],
)
def test_closed_skip_mode_still_resolves_explicitly_configured_names(configuration, selected):
    source = '#include "config.h"\n#ifdef FEATURE\nint on;\n#endif\n'
    result = preprocess_source(source, configuration=configuration, skip_includes=True)
    assert result.complete
    assert ("int on;" in result.source) is selected


def test_closed_skip_mode_keeps_source_order_state():
    before = preprocess_source(
        '#define ON 1\n#undef OFF\n#include "c.h"\n#if ON && !defined(OFF)\nint on;\n#endif\n',
        configuration=CLOSED,
        skip_includes=True,
    )
    assert before.complete
    assert "int on;" in before.source

    after = preprocess_source(
        '#include "c.h"\n#undef LATER\n#ifdef LATER\nint on;\n#endif\n',
        configuration=CLOSED,
        skip_includes=True,
    )
    assert after.complete
    assert "int on;" not in after.source


def test_closed_skip_mode_defaults_names_before_the_first_skipped_include():
    result = preprocess_source(
        '#ifdef EARLY\nint early;\n#endif\n#include "c.h"\nint x;\n',
        configuration=CLOSED,
        skip_includes=True,
    )
    assert result.complete
    assert "int early;" not in result.source


def test_closed_mode_without_reachable_skipped_include_is_unchanged():
    for source in (
        "#ifdef FEATURE\nint on;\n#endif\n",
        '#if 0\n#include "never.h"\n#endif\n#ifdef FEATURE\nint on;\n#endif\n',
    ):
        result = preprocess_source(source, configuration=CLOSED, skip_includes=True)
        assert result.complete
        assert "int on;" not in result.source


def test_skip_mode_ignores_inactive_includes():
    result = preprocess_source(
        '#if 0\n#include "never.h"\n#endif\n#include "live.h"\n', skip_includes=True
    )
    assert result.complete
    assert result.skipped_includes == (SkippedInclude("include", '"live.h"', SourceLocation(4)),)


def test_skip_mode_records_computed_include_operand_unexpanded():
    result = preprocess_source('#define HEADER "x.h"\n#include HEADER\n', skip_includes=True)
    assert result.complete
    assert result.skipped_includes == (SkippedInclude("include", "HEADER", SourceLocation(2)),)


def test_skip_mode_rejects_include_without_operand():
    result = preprocess_source("#include\nint x;\n", skip_includes=True)
    assert not result.complete
    assert result.skipped_includes == ()
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert result.incomplete[0].message == "#include expects a header name"


def test_skip_mode_incomplete_result_is_atomic():
    result = preprocess_source('#include "a.h"\n#pragma once\n', skip_includes=True)
    assert not result.complete
    assert result.source is None
    assert result.skipped_includes == ()


def test_skip_mode_composes_with_has_include():
    source = "#if __has_include(<opt.h>)\n#include <opt.h>\n#endif\nint x;\n"
    result = preprocess_source(source, skip_includes=True, include_query=lambda query: True)
    assert result.complete
    assert result.skipped_includes == (SkippedInclude("include", "<opt.h>", SourceLocation(2)),)


@pytest.mark.parametrize("value", [1, "yes", None])
def test_skip_includes_requires_bool(value):
    with pytest.raises(cpre.AnalysisError) as caught:
        preprocess_source("int x;\n", skip_includes=value)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


@pytest.mark.parametrize(
    "directive",
    ['#include "local.h"', "#include <system.h>", "#include_next <next.h>", '#import "objc.h"'],
)
def test_cli_skip_includes(tmp_path, capsys, directive):
    source = tmp_path / "source.c"
    _write(source, f"{directive}\r\n#ifdef FEATURE\r\nint on;\r\n#endif\r\n")

    assert main(["preprocess", str(source), "-DFEATURE"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "include processing is not supported" in captured.err

    assert main(["preprocess", str(source), "-DFEATURE", "--skip-includes"]) == 0
    captured = capsys.readouterr()
    assert (
        captured.out
        == " " * len(directive) + "\r\n" + " " * 14 + "\r\nint on;\r\n" + " " * 6 + "\r\n"
    )
    operand = directive.split(None, 1)[1]
    kind = directive.split(None, 1)[0][1:]
    assert captured.err == f"{source}: line 1: skipped #{kind} {operand}\n"


def test_cli_skip_includes_reports_multiple_and_compacts(tmp_path, capsys):
    source = tmp_path / "source.c"
    _write(source, '#include "a.h"\n#include <b.h>\nint x;\n')
    assert main(["preprocess", str(source), "--skip-includes", "--compact"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "int x;\n"
    assert captured.err.splitlines() == [
        f'{source}: line 1: skipped #include "a.h"',
        f"{source}: line 2: skipped #include <b.h>",
    ]


def test_cli_closed_skip_mode_does_not_guess_header_macros(tmp_path, capsys):
    source = tmp_path / "source.c"
    _write(source, '#include "config.h"\n#ifdef FEATURE\nint on;\n#endif\n')

    arguments = ["preprocess", str(source), "--skip-includes", "--unknown-names", "undefined"]
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "condition is not determined" in captured.err

    assert main([*arguments, "-UFEATURE", "--compact"]) == 0
    assert capsys.readouterr().out == ""
    assert main([*arguments, "-DFEATURE", "--compact"]) == 0
    assert capsys.readouterr().out == "int on;\n"
