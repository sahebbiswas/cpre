"""Color, glyphs and layout of the text reports (``cpre._style`` and the report builders)."""

from __future__ import annotations

import io
import re

import pytest

import cpre
from cpre import _style, reporting, text_reports
from cpre._style import (
    PLAIN,
    Style,
    detect_style,
    diff_segments,
    paint_segments,
    resolve_color,
    supports_unicode,
)
from cpre.cli import main
from cpre.macro_analysis import analyze_macros

ANSI = re.compile(r"\033\[[0-9;]*m")

SOURCE = """\
#define A 1
#if defined(X) && !defined(X)
int dead;
#endif
#if FLAG
#  if FLAG
int redundant;
#  endif
#endif
#if DUP && DUP
int simplifiable;
#endif
#define M (A && A)
"""


class _Stream:
    def __init__(self, *, tty: bool = False, encoding: str | None = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


def _report(style: Style, *, verbose: bool = False) -> str:
    analysis = cpre.analyze_source(SOURCE)
    macros = analyze_macros(SOURCE)
    return text_reports.render_combined_report(analysis.tree, macros, verbose=verbose, style=style)[
        0
    ]


# --- detection --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "env", "tty", "expected"),
    [
        ("always", {"NO_COLOR": "1"}, False, True),
        ("never", {"FORCE_COLOR": "1"}, True, False),
        ("auto", {}, True, True),
        ("auto", {}, False, False),
        ("auto", {"NO_COLOR": "1"}, True, False),
        ("auto", {"NO_COLOR": ""}, True, True),
        ("auto", {"FORCE_COLOR": "1"}, False, True),
        ("auto", {"FORCE_COLOR": "0"}, False, False),
        ("auto", {"TERM": "dumb"}, True, False),
        ("auto", {"TERM": "dumb", "FORCE_COLOR": "1"}, True, True),
        ("auto", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, True, False),
    ],
)
def test_resolve_color(mode, env, tty, expected):
    assert resolve_color(mode, _Stream(tty=tty), env) is expected


def test_resolve_color_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown color mode"):
        resolve_color("sometimes", _Stream(), {})


@pytest.mark.parametrize(
    ("encoding", "env", "expected"),
    [
        ("utf-8", {}, True),
        ("UTF-8", {}, True),
        ("ascii", {}, False),
        ("cp1252", {}, False),
        ("no-such-codec", {}, False),
        (None, {}, False),
        ("utf-8", {"TERM": "dumb"}, False),
    ],
)
def test_supports_unicode(encoding, env, expected):
    assert supports_unicode(_Stream(encoding=encoding), env) is expected


def test_detect_style_combines_both_switches():
    style = detect_style(_Stream(tty=True, encoding="ascii"), "auto", {})
    assert style == Style(color=True, unicode=False)
    assert detect_style(_Stream(), "always", {}) == Style(color=True, unicode=True)


# --- Style ------------------------------------------------------------------------------


def test_paint_is_a_no_op_without_color_or_text():
    assert PLAIN.paint("x", "red") == "x"
    assert Style(color=True).paint("", "red") == ""
    assert Style(color=True).paint("x", "red") == "\033[31mx\033[0m"


def test_gray_is_the_dim_attribute_not_bright_black():
    assert Style(color=True).paint("x", "gray") == "\033[2mx\033[0m"


def test_glyphs_have_ascii_fallbacks():
    fancy, plain = Style(unicode=True), Style()
    assert (fancy.guide, fancy.arrow, fancy.separator, fancy.ellipsis) == ("│ ", "→", " · ", "…")
    assert (plain.guide, plain.arrow, plain.separator, plain.ellipsis) == ("| ", "->", ", ", "...")
    assert fancy.bar(0.5, 10) == "▕█████░░░░░▏"
    assert plain.bar(0.5, 10) == "[#####-----]"
    assert plain.bar(2.0, 4) == "[####]"
    assert plain.bar(-1.0, 4) == "[----]"


# --- token-level highlighting -----------------------------------------------------------


def _joined(segments):
    return "".join(text for text, _ in segments)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("(A && A)", "(!!A)"),
        ("A || (A && B)", "A"),
        ("X && !X", "0"),
        ("", "1"),
        ("A", ""),
        ("(1 || A) && B", "(!!B)"),
    ],
)
def test_diff_segments_reassemble_each_side(before, after):
    left, right = diff_segments(before, after)
    assert _joined(left) == before
    assert _joined(right) == after


def test_diff_segments_mark_only_the_changed_tokens():
    left, right = diff_segments("(A && A)", "(A)")
    assert left == [("(A", False), (" && A", True), (")", False)]
    assert right == [("(A)", False)]


def test_diff_segments_leave_unrelated_sides_unmarked():
    left, right = diff_segments("X && !X", "0")
    assert left == [("X && !X", False)]
    assert right == [("0", False)]


def test_paint_segments_use_the_stronger_role_for_changes():
    painted = paint_segments(
        Style(color=True), [("a", False), ("b", True)], "before", "before_changed"
    )
    assert painted == "\033[31ma\033[0m\033[1;31mb\033[0m"


# --- layout -----------------------------------------------------------------------------


def test_plain_ascii_report_layout():
    assert _report(Style()) == (
        "Conditionals\n"
        "L2  DEAD       #if defined(X) && !defined(X)\n"
        "  why  condition contradicts its parent or earlier branches\n"
        "  - X && !X\n"
        "  + 0  (always false)\n"
        "L5  ok         #if FLAG\n"
        "| L6  REDUNDANT  #if FLAG\n"
        "|   why  condition is always true in this branch context\n"
        "|   - FLAG\n"
        "|   + 1  (always true in context)\n"
        "L10 SIMPLIFY   #if DUP && DUP\n"
        "  - DUP && DUP\n"
        "  + DUP\n"
        "\n"
        "Macros\n"
        "L13 SIMPLIFY   #define M\n"
        "   - (A && A)\n"
        "   + (!!A)  (proven equivalent)"
    )


def test_unicode_report_differs_from_ascii_only_in_glyphs():
    ascii_report = _report(Style())
    unicode_report = _report(Style(unicode=True))
    assert unicode_report != ascii_report
    assert unicode_report.replace("│", "|") == ascii_report


def test_color_only_wraps_text_that_is_already_there():
    for unicode in (False, True):
        for verbose in (False, True):
            colored = _report(Style(color=True, unicode=unicode), verbose=verbose)
            plain = _report(Style(unicode=unicode), verbose=verbose)
            assert "\033[" in colored
            assert ANSI.sub("", colored) == plain


def test_verbose_report_adds_skipped_macros_and_effective_conditions():
    default = _report(Style())
    verbose = _report(Style(), verbose=True)
    assert "#define A" not in default
    assert "L1  skip       #define A  replacement list contains no Boolean operators" in verbose
    assert "effective" not in default
    assert "|   effective  FLAG" in verbose


def test_color_roles_follow_one_meaning_per_color():
    report = _report(Style(color=True))
    assert "\033[1;31mDEAD\033[0m" in report
    assert "\033[1;33mREDUNDANT\033[0m" in report
    assert "\033[1;32mSIMPLIFY\033[0m" in report
    assert "\033[31m-\033[0m" in report
    assert "\033[32m+\033[0m" in report
    # Green appears only on a transformation's result (a "+" line) or a SIMPLIFY badge.
    for line in report.splitlines():
        if "\033[32m" in line or "\033[1;32m" in line:
            plain = ANSI.sub("", line).lstrip("| ")
            assert plain.startswith("+") or " SIMPLIFY " in plain, plain
        if "\033[31m" in line or "\033[1;31m" in line:
            plain = ANSI.sub("", line).lstrip("| ")
            assert plain.startswith("-") or " DEAD " in plain, plain


def test_changed_tokens_are_emphasized_in_color():
    report = _report(Style(color=True))
    assert "\033[1;31mA && \033[0m" in report  # the dropped tokens of (A && A)
    assert "\033[1;32m!!\033[0m" in report  # the added tokens of (!!A)


def test_line_labels_are_aligned_to_the_widest_line():
    report = _report(Style())
    assert "L5  ok         #if FLAG" in report
    assert "L10 SIMPLIFY   #if DUP && DUP" in report
    assert "L13 SIMPLIFY   #define M" in report


def test_else_branch_shows_only_its_effective_condition_in_verbose():
    tree = cpre.analyze_source("#if A\nint a;\n#else\nint b;\n#endif\n").tree
    assert reporting.format_report(tree, verbose=True) == (
        "L1 ok         #if A\nL3 ok         #else\n  effective  !A"
    )
    assert reporting.format_report(tree, verbose=False) == (
        "No changed, dead, or redundant conditional directives found."
    )


def test_incomplete_analysis_block_uses_badges():
    result = cpre.analyze_source("#if A && B\n#endif\n", options=cpre.AnalysisOptions(max_atoms=1))
    lines = text_reports.incomplete_lines(result.incomplete_components, style=Style())
    assert lines[0].startswith("Incomplete analysis")
    assert lines[1] == "L1-2 INCOMPLETE #if A && B"
    assert lines[2].lstrip().startswith("why  analysis limit exceeded")
    colored = text_reports.incomplete_lines(result.incomplete_components, style=Style(color=True))
    assert "\033[1;33mINCOMPLETE\033[0m" in colored[1]


def test_legacy_color_argument_still_selects_plain_ascii_colored_text():
    tree = cpre.analyze_source("#if 0\n#endif\n").tree
    colored = reporting.format_report(tree, color=True)
    assert "\033[1;31mDEAD\033[0m" in colored
    assert "│" not in colored
    assert reporting.colored("x", "red", True) == "\033[31mx\033[0m"
    assert reporting.colored("x", "red", False) == "x"
    with pytest.raises(KeyError):
        reporting.colored("x", "not-a-color", True)


# --- counts and summary -----------------------------------------------------------------


def test_finding_counts_cover_dead_redundant_and_simplifiable():
    tree = cpre.analyze_source(SOURCE).tree
    counts = reporting.count_findings(tree)
    assert counts == reporting.FindingCounts(dead=1, redundant=1, simplifiable=1)
    assert counts.failing == 2
    assert counts.total == 3
    both = text_reports.file_findings(tree, analyze_macros(SOURCE))
    assert both.macros == 1 and both.total == 4
    assert (counts + counts).dead == 2


def test_summary_line_is_empty_without_findings_and_pluralizes():
    assert text_reports.summary_line(reporting.FindingCounts()) == ""
    one = text_reports.summary_line(reporting.FindingCounts(dead=1, simplifiable=1, macros=1))
    assert one == "Summary  1 dead, 1 simplifiable condition, 1 simplifiable macro"
    many = text_reports.summary_line(
        reporting.FindingCounts(redundant=2, simplifiable=2, macros=3), Style(unicode=True)
    )
    assert many == "Summary  2 redundant · 2 simplifiable conditions · 3 simplifiable macros"


# --- the command line -------------------------------------------------------------------


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "sample.c"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_cli_color_always_and_never(source, capsys):
    assert main([str(source), "--color", "always"]) == 0
    colored = capsys.readouterr().out
    assert "\033[1;31mDEAD\033[0m" in colored

    assert main([str(source), "--color", "never"]) == 0
    plain = capsys.readouterr().out
    assert "\033[" not in plain
    assert ANSI.sub("", colored) == plain


def test_cli_auto_color_honors_the_environment(source, capsys, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    main([str(source)])
    assert "\033[" not in capsys.readouterr().out  # captured output is not a terminal

    monkeypatch.setenv("FORCE_COLOR", "1")
    main([str(source)])
    assert "\033[" in capsys.readouterr().out

    monkeypatch.setenv("NO_COLOR", "1")
    main([str(source)])
    assert "\033[" not in capsys.readouterr().out


def test_cli_ends_with_the_summary_line(source, capsys):
    main([str(source)])
    out = capsys.readouterr().out
    assert out.endswith(
        "\n\nSummary  1 dead · 1 redundant · 1 simplifiable condition · 1 simplifiable macro\n"
    )


def test_cli_has_no_summary_without_findings(tmp_path, capsys):
    path = tmp_path / "clean.c"
    path.write_text("#if A\n#endif\n", encoding="utf-8")
    main([str(path)])
    out = capsys.readouterr().out
    assert "Summary" not in out


def test_cli_uses_ascii_glyphs_when_stdout_cannot_encode_them(source, capsys, monkeypatch):
    class _Ascii(io.StringIO):
        encoding = "ascii"

    stream = _Ascii()
    monkeypatch.setattr("sys.stdout", stream)
    main([str(source)])
    out = stream.getvalue()
    assert "│" not in out and "·" not in out
    assert "| L6  REDUNDANT" in out
    assert out.rstrip().endswith("1 simplifiable macro")


def test_cli_batch_report_is_headed_per_file_and_summarized_once(tmp_path, capsys):
    first = tmp_path / "a.c"
    second = tmp_path / "b.c"
    first.write_text("#if 0\n#endif\n", encoding="utf-8")
    second.write_text("#if 0\n#endif\n", encoding="utf-8")
    main([str(first), str(second)])
    out = capsys.readouterr().out
    assert f"== {first} ==" in out and f"== {second} ==" in out
    assert out.count("Summary") == 1
    assert "2 dead" in out
    assert f"\n\n== {second} ==" in out  # one blank line between files


def test_summary_counts_use_thousands_separators():
    line = text_reports.summary_line(reporting.FindingCounts(redundant=24736, simplifiable=2731))
    assert line == "Summary  24,736 redundant, 2,731 simplifiable conditions"


# --- Windows consoles: ANSI only once virtual-terminal processing is on --------------------


class _Console:
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1


def test_ansi_capable_needs_a_terminal():
    assert _style.ansi_capable(_Console())
    assert not _style.ansi_capable(io.StringIO())


def test_windows_console_without_vt_support_gets_no_automatic_color(monkeypatch):
    monkeypatch.setattr(_style, "_is_windows", lambda: True)
    monkeypatch.setattr(_style, "_enable_windows_vt", lambda stream: False)
    assert not _style.resolve_color("auto", _Console(), {})
    assert _style.resolve_color("always", _Console(), {})  # explicit requests are honoured
    assert _style.resolve_color("auto", _Console(), {"FORCE_COLOR": "1"})


def test_windows_console_with_vt_support_gets_automatic_color(monkeypatch):
    monkeypatch.setattr(_style, "_is_windows", lambda: True)
    monkeypatch.setattr(_style, "_enable_windows_vt", lambda stream: True)
    assert _style.resolve_color("auto", _Console(), {})


def test_live_progress_needs_ansi_support_on_windows(monkeypatch):
    from cpre.cli._progress import Progress

    monkeypatch.setattr(_style, "_is_windows", lambda: True)
    monkeypatch.setattr(_style, "_enable_windows_vt", lambda stream: False)
    plain = Progress.create("always", batch=True, stream=_Console(), env={})  # type: ignore[arg-type]
    assert plain._kind == "lines"
    monkeypatch.setattr(_style, "_enable_windows_vt", lambda stream: True)
    live = Progress.create("always", batch=True, stream=_Console(), env={})  # type: ignore[arg-type]
    assert live._kind == "live"
