"""Progress for multi-file runs: the reporter on its own, discovery, and the command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cpre.cli.analyze as analyze_module
from cpre._style import Style
from cpre.cli import main
from cpre.cli._progress import Progress, format_duration, paused, shorten_path
from cpre.cli._progress_text import (
    Snapshot,
    eta_seconds,
    interrupted_text,
    line_text,
    plural,
    status_text,
    strip_ansi,
    summary_text,
)
from cpre.discovery import source_paths

CLEAR = "\r\033[2K"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Stream:
    def __init__(self, *, tty: bool = True, encoding: str | None = "utf-8") -> None:
        self.text = ""
        self.encoding = encoding
        self._tty = tty
        self.broken = False

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> int:
        if self.broken:
            raise BrokenPipeError
        self.text += text
        return len(text)

    def flush(self) -> None:
        if self.broken:
            raise BrokenPipeError

    def fileno(self) -> int:
        raise OSError("not a real file")


def _files(tmp_path: Path, count: int, size: int = 10) -> list[Path]:
    paths = []
    for index in range(count):
        path = tmp_path / f"f{index}.c"
        path.write_text("x" * size, encoding="utf-8")
        paths.append(path)
    return paths


def _progress(kind: str = "live", **options):
    clock, stream = Clock(), Stream()
    options.setdefault("show_after", 0.0)
    options.setdefault("redraw", 0.0)
    progress = Progress(
        stream,
        kind=kind,
        clock=clock,
        threaded=False,
        columns=lambda: 100,
        style=Style(unicode=True),
        **options,
    )
    return progress, clock, stream


# --- formatting helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "precise", "expected"),
    [
        (0.4, False, "0s"),
        (9.9, False, "9s"),
        (12.44, True, "12.4s"),
        (60, False, "1m00s"),
        (125, False, "2m05s"),
        (3725, False, "1h02m"),
    ],
)
def test_format_duration(seconds, precise, expected):
    assert format_duration(seconds, precise=precise) == expected


def test_shorten_path_keeps_the_end_of_the_path():
    assert shorten_path("src/net/tcp.c", 40) == "src/net/tcp.c"
    assert shorten_path("src/net/tcp.c", 8) == "…t/tcp.c"
    assert shorten_path("src/net/very/deep/tcp.c", 12) == "…/deep/tcp.c"
    assert shorten_path("src/net/tcp.c", 8, "...") == "...tcp.c"
    assert shorten_path("abcdef", 1) == "f"
    assert shorten_path("abcdef", 0) == ""


# --- the live status line ---------------------------------------------------------------


def test_live_line_shows_counts_bar_percentage_and_current_file(tmp_path):
    paths = _files(tmp_path, 4)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        assert stream.text.endswith("\033[K")
        assert "0/4" in stream.text and "▕░░░░░░░░░░▏" in stream.text and "  0%" in stream.text
        assert "f0.c" in stream.text
        clock.advance(1)
        progress.file_done(2)
        progress.file_started(paths[1])
        assert "1/4" in stream.text and " 25%" in stream.text and "2 found" in stream.text
        assert "f1.c" in stream.text


def test_live_line_is_one_carriage_return_redraw_never_a_new_line(tmp_path):
    paths = _files(tmp_path, 3)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        for path in paths:
            progress.file_started(path)
            clock.advance(0.5)
            progress.file_done()
        assert "\n" not in stream.text
        assert stream.text.count("\r") >= 3


def test_redraws_are_throttled(tmp_path):
    paths = _files(tmp_path, 2)
    progress, clock, stream = _progress(redraw=0.1)
    with progress:
        progress.begin(paths)
        before = stream.text.count("\r")
        for _ in range(50):
            progress.tick()  # no time has passed
        assert stream.text.count("\r") == before
        clock.advance(0.2)
        progress.tick()
        assert stream.text.count("\r") == before + 1


def test_status_waits_for_show_after_so_quick_runs_stay_silent(tmp_path):
    paths = _files(tmp_path, 2)
    progress, clock, stream = _progress(show_after=0.5)
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        clock.advance(0.3)
        progress.file_done()
        progress.file_started(paths[1])
        progress.file_done()
    assert stream.text == ""  # nothing drawn, and no closing summary for a quick run


def test_scanning_phase_counts_files_and_time():
    progress, clock, stream = _progress()
    with progress:
        progress.scanning(1204)
        assert "scanning… 1,204 files" in stream.text
        clock.advance(4)
        progress.tick()
        assert "4s" in stream.text.rsplit("\r", 1)[-1]


def test_eta_needs_enough_time_and_progress_and_is_size_weighted(tmp_path):
    big = tmp_path / "big.c"
    big.write_text("x" * 900, encoding="utf-8")
    small = _files(tmp_path, 9, size=11)  # 99 bytes
    paths = [big, *small]
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        progress.file_started(big)
        clock.advance(2)
        progress.file_done()
        assert "~" not in stream.text.rsplit("\r", 1)[-1]  # under three seconds
        clock.advance(1.5)
        progress.file_started(small[0])
        progress.tick(force=True)
        last = stream.text.rsplit("\r", 1)[-1]
        # One of ten files, but 90% of the bytes, is done: only a short time remains.
        assert " 10%" in last
        assert "~0s" in last or "~1s" in last


def test_slow_file_shows_its_elapsed_time(tmp_path):
    paths = _files(tmp_path, 2)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        clock.advance(7)
        progress.tick()
        assert "f0.c 7s" in stream.text.rsplit("\r", 1)[-1]


def test_long_paths_are_shortened_to_the_terminal_width(tmp_path):
    deep = tmp_path / ("d" * 40) / ("e" * 40)
    deep.mkdir(parents=True)
    path = deep / "file.c"
    path.write_text("x", encoding="utf-8")
    clock, stream = Clock(), Stream()
    progress = Progress(
        stream,
        kind="live",
        show_after=0,
        clock=clock,
        threaded=False,
        columns=lambda: 60,
        style=Style(unicode=True),
        redraw=0,
    )
    with progress:
        progress.begin([path])
        progress.file_started(path)
        line = stream.text.split("\r")[-1].removesuffix("\033[K")
    assert len(line) <= 59
    assert "…" in line and line.rstrip().endswith("file.c")


def test_narrow_terminals_drop_the_bar(tmp_path):
    paths = _files(tmp_path, 2)
    clock, stream = Clock(), Stream()
    progress = Progress(
        stream,
        kind="live",
        show_after=0,
        clock=clock,
        threaded=False,
        columns=lambda: 40,
        redraw=0,
    )
    with progress:
        progress.begin(paths)
    assert "0/2" in stream.text and "[-" not in stream.text and "[#" not in stream.text


def test_color_paints_the_bar_and_dims_the_path(tmp_path):
    paths = _files(tmp_path, 2)
    clock, stream = Clock(), Stream()
    progress = Progress(
        stream,
        kind="live",
        show_after=0,
        clock=clock,
        threaded=False,
        columns=lambda: 100,
        style=Style(color=True, unicode=True),
        redraw=0,
    )
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
    assert "\033[36m▕" in stream.text
    assert "\033[2m" in stream.text


# --- cooperating with other output ------------------------------------------------------


def test_paused_clears_the_line_and_redraws_it_afterwards(tmp_path, capsys):
    paths = _files(tmp_path, 2)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        stream.text = ""
        with paused():
            assert stream.text == CLEAR
            print("a report")
        assert stream.text.startswith(CLEAR) and stream.text.endswith("\033[K")
        assert "0/2" in stream.text
    assert capsys.readouterr().out == "a report\n"


def test_paused_is_a_no_op_without_an_active_reporter(capsys):
    with paused():
        print("hello")
    assert capsys.readouterr().out == "hello\n"


def test_inert_reporter_never_writes(tmp_path):
    clock, stream = Clock(), Stream()
    progress = Progress(stream, kind="off", clock=clock, threaded=False)
    paths = _files(tmp_path, 2)
    with progress:
        progress.scanning(2)
        progress.begin(paths)
        for path in paths:
            progress.file_started(path)
            progress.file_done(1)
        with paused():
            pass
    assert stream.text == "" and not progress.active


def test_a_broken_stream_turns_progress_off_without_failing_the_run(tmp_path):
    paths = _files(tmp_path, 2)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        stream.broken = True
        progress.file_started(paths[0])
        clock.advance(1)
        progress.file_done()
        assert not progress.active


# --- heartbeat lines and summaries ------------------------------------------------------


def test_heartbeat_lines_are_plain_and_spaced_by_the_interval(tmp_path):
    paths = _files(tmp_path, 4)
    progress, clock, stream = _progress("lines", interval=10.0)
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        clock.advance(4)
        progress.file_done(3)
        assert stream.text == ""
        clock.advance(7)
        progress.file_started(paths[1])
        assert stream.text == "cpre: 1/4 files (25%), 3 findings, 11s elapsed\n"
        progress.file_done(0, failed=True)
        clock.advance(3)
        progress.tick()
        assert stream.text.count("\n") == 1
    assert "\r" not in stream.text and "\033" not in stream.text
    assert stream.text.splitlines()[-1] == "cpre: 2 files in 14.0s, 3 findings, 1 failed"


def test_heartbeat_while_scanning():
    progress, clock, stream = _progress("lines", interval=5.0)
    with progress:
        progress.scanning(40)
        clock.advance(6)
        progress.scanning(75)
    assert stream.text.splitlines()[0] == "cpre: scanning, 75 files found, 6s"


def test_summary_is_printed_after_a_slow_run_but_not_after_a_quick_one(tmp_path):
    paths = _files(tmp_path, 3)
    progress, clock, stream = _progress()
    with progress:
        progress.begin(paths)
        for path in paths:
            progress.file_started(path)
            clock.advance(1)
            progress.file_done(1)
    assert stream.text.endswith(CLEAR + "cpre: 3 files in 3.0s, 3 findings\n")


def test_an_announced_run_always_ends_with_a_summary(tmp_path):
    paths = _files(tmp_path, 1)
    progress, clock, stream = _progress("lines", announce=True)
    with progress:
        progress.begin(paths)
        progress.file_started(paths[0])
        progress.file_done()
    assert stream.text == "cpre: 1 file in 0.0s\n"


def test_no_summary_when_the_run_fails_with_an_exit(tmp_path):
    progress, clock, stream = _progress(announce=True)
    with pytest.raises(SystemExit):
        with progress:
            progress.scanning(3)
            raise SystemExit(2)
    assert stream.text.endswith(CLEAR) and "cpre:" not in stream.text


def test_interrupt_reports_how_far_the_run_got(tmp_path):
    paths = _files(tmp_path, 5)
    progress, clock, stream = _progress()
    with pytest.raises(KeyboardInterrupt):
        with progress:
            progress.begin(paths)
            progress.file_started(paths[0])
            progress.file_done()
            progress.file_started(paths[1])
            raise KeyboardInterrupt
    assert stream.text.endswith(CLEAR + "cpre: interrupted after 1/5 files\n")


def test_interrupt_while_scanning_says_so():
    progress, clock, stream = _progress()
    with pytest.raises(KeyboardInterrupt):
        with progress:
            progress.scanning(12)
            raise KeyboardInterrupt
    assert stream.text.endswith("cpre: interrupted while scanning (12 files found)\n")


def test_the_redraw_thread_starts_and_stops(tmp_path):
    clock, stream = Clock(), Stream()
    progress = Progress(stream, kind="live", show_after=0, clock=clock, threaded=True)
    with progress:
        assert progress._ticker is not None and progress._ticker.is_alive()
    assert not progress._ticker.is_alive()


# --- choosing a presentation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "batch", "tty", "env", "kind"),
    [
        ("never", True, True, {}, "off"),
        ("auto", False, True, {}, "off"),
        ("always", False, True, {}, "off"),
        ("auto", True, False, {}, "off"),
        ("auto", True, True, {}, "live"),
        ("auto", True, True, {"TERM": "dumb"}, "lines"),
        ("always", True, False, {}, "lines"),
        ("always", True, True, {}, "live"),
        ("always", True, True, {"TERM": "dumb"}, "lines"),
    ],
)
def test_create_picks_the_presentation(mode, batch, tty, env, kind):
    progress = Progress.create(mode, batch=batch, stream=Stream(tty=tty), env=env)
    assert progress._kind == kind


def test_always_shows_immediately_and_auto_waits():
    always = Progress.create("always", batch=True, stream=Stream(), env={})
    auto = Progress.create("auto", batch=True, stream=Stream(), env={})
    assert always._show_after == 0 and always._announce
    assert auto._show_after == 0.5 and not auto._announce


# --- discovery --------------------------------------------------------------------------


def test_discovery_reports_the_running_count_and_keeps_its_order(tmp_path):
    for name in ("b/z.c", "a/y.h", "a/x.c", "c/skip.txt"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("", encoding="utf-8")
    seen: list[int] = []
    paths = source_paths([tmp_path], True, on_found=seen.append)
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == ["a/x.c", "a/y.h", "b/z.c"]
    assert seen and seen[-1] == 3
    assert source_paths([tmp_path], True) == paths


def test_discovery_reports_progress_during_a_large_walk(tmp_path):
    for index in range(600):
        (tmp_path / f"n{index}.txt").write_text("", encoding="utf-8")
    (tmp_path / "only.c").write_text("", encoding="utf-8")
    seen: list[int] = []
    source_paths([tmp_path], True, on_found=seen.append)
    assert len(seen) >= 3  # called while walking, not only at the end
    assert seen[-1] == 1


# --- the command line -------------------------------------------------------------------


FINDING = "#if A\n#if A\n#endif\n#endif\n"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    (root / "sub").mkdir(parents=True)
    (root / "a.c").write_text(FINDING, encoding="utf-8")
    (root / "b.c").write_text("#if B\n#endif\n", encoding="utf-8")
    (root / "sub" / "c.h").write_text(FINDING, encoding="utf-8")
    return root


def _run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_progress_never_changes_standard_output(tree, capsys):
    _, quiet, quiet_err = _run(capsys, "--recursive", "--progress", "never", str(tree))
    _, loud, loud_err = _run(capsys, "--recursive", "--progress", "always", str(tree))
    assert quiet == loud
    assert quiet_err == ""
    assert loud_err.startswith("cpre: 3 files in ") and loud_err.endswith(", 2 findings\n")


def test_json_stays_valid_with_progress(tree, capsys):
    _, quiet, _ = _run(capsys, "--recursive", "--json", "--progress", "never", str(tree))
    _, loud, err = _run(capsys, "--recursive", "--json", "--progress", "always", str(tree))
    assert json.loads(loud) == json.loads(quiet)
    assert err.startswith("cpre: 3 files in ")


def test_sarif_stays_valid_with_progress(tree, capsys):
    _, out, err = _run(capsys, "--recursive", "--sarif", "--progress", "always", str(tree))
    assert json.loads(out)["version"] == "2.1.0"
    assert err.startswith("cpre: 3 files")


def test_auto_progress_is_silent_when_stderr_is_not_a_terminal(tree, capsys):
    _, _, err = _run(capsys, "--recursive", str(tree))
    assert err == ""


def test_single_file_never_shows_progress(tree, capsys):
    _, _, err = _run(capsys, "--progress", "always", str(tree / "a.c"))
    assert err == ""


def test_text_results_are_written_as_each_file_finishes(tree, capsys, monkeypatch):
    real = analyze_module.analyze_source
    written_before: list[str] = []

    def spying(source, **kwargs):
        written_before.append(capsys.readouterr().out)
        return real(source, **kwargs)

    monkeypatch.setattr(analyze_module, "analyze_source", spying)
    code = main(["--recursive", "--no-macros", str(tree)])
    assert code == 0
    rest = capsys.readouterr().out
    # Before the second file was analyzed the first file's report was already out.
    assert written_before[0] == ""
    assert "a.c ==" in written_before[1]
    assert "b.c" not in written_before[1] and "c.h ==" in rest


def test_streamed_batch_output_equals_the_files_joined_by_blank_lines(tree, capsys):
    _, out, _ = _run(capsys, "--recursive", str(tree))
    a, c = tree / "a.c", tree / "sub" / "c.h"
    assert out.startswith(f"== {a} ==\n")
    assert f"\n\n== {c} ==\n" in out
    assert out.endswith("2 redundant\n")
    assert "b.c" not in out  # the unchanged file is omitted, as before


def test_interrupt_keeps_what_was_written_and_exits_130(tree, capsys, monkeypatch):
    real = analyze_module.analyze_source
    calls = []

    def interrupting(source, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(source, **kwargs)

    monkeypatch.setattr(analyze_module, "analyze_source", interrupting)
    code, out, err = _run(capsys, "--recursive", "--progress", "always", "--no-macros", str(tree))
    assert code == 130
    assert "a.c ==" in out and "c.h" not in out
    assert err.endswith("cpre: interrupted after 1/3 files\n")


def test_interrupt_without_progress_is_still_status_130(tree, capsys, monkeypatch):
    def interrupting(source, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(analyze_module, "analyze_source", interrupting)
    code, out, err = _run(capsys, "--no-macros", str(tree / "a.c"))
    assert (code, out) == (130, "")
    assert "Traceback" not in err


def test_failures_are_reported_and_counted(tree, capsys):
    (tree / "bad.c").write_bytes(b"\xff\xfe\x00 not utf-8 \x80")
    code, out, err = _run(capsys, "--recursive", "--progress", "always", str(tree))
    assert code == 2
    assert f"{tree / 'bad.c'}: " in err
    assert err.splitlines()[-1].endswith("1 failed")
    assert "== " in out  # the other files were still reported


def test_scanning_a_missing_directory_leaves_no_progress_behind(tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--recursive",
                "--progress",
                "always",
                str(tmp_path / "missing"),
                str(tmp_path / "also-missing"),
            ]
        )
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "no such file or directory" in err and "cpre:" not in err


def test_telemetry_lines_and_progress_do_not_interfere(tree, capsys):
    code, _, err = _run(capsys, "--recursive", "--telemetry", "--progress", "always", str(tree))
    assert code == 0
    assert err.count(": telemetry: ") >= 3
    assert err.splitlines()[-1].startswith("cpre: 3 files in ")


def test_simplify_macros_streams_and_reports_progress(tmp_path, capsys):
    (tmp_path / "a.c").write_text("#define M (A && A)\n", encoding="utf-8")
    (tmp_path / "b.c").write_text("#define N (B || B)\n", encoding="utf-8")
    argv = ["simplify-macros", "--recursive", str(tmp_path)]
    _, quiet, _ = _run(capsys, *argv, "--progress", "never")
    code, loud, err = _run(capsys, *argv, "--progress", "always")
    assert code == 0 and quiet == loud
    assert quiet.endswith("\n\nSummary  2 simplifiable macros\n")
    assert err.startswith("cpre: 2 files in ") and err.endswith(", 2 findings\n")


def test_simplify_macros_diff_is_identical_with_progress(tmp_path, capsys):
    (tmp_path / "a.c").write_text("#define M (A && A)\n", encoding="utf-8")
    argv = ["simplify-macros", "--recursive", "--diff", str(tmp_path)]
    _, quiet, _ = _run(capsys, *argv, "--progress", "never")
    _, loud, err = _run(capsys, *argv, "--progress", "always")
    assert quiet == loud and quiet.startswith("--- ")
    assert err.startswith("cpre: 1 file in ")


def test_simplify_macros_rewrite_adds_no_summary(tmp_path, capsys):
    path = tmp_path / "a.c"
    path.write_text("#define M (A && A)\n", encoding="utf-8")
    _, out, _ = _run(capsys, "simplify-macros", "--rewrite", "--recursive", str(tmp_path))
    assert "REWRITTEN" in out and "Summary" not in out
    assert path.read_text(encoding="utf-8") == "#define M (!!A)\n"


def test_options_are_validated():
    with pytest.raises(SystemExit):
        main(["--progress", "sometimes", "x.c"])
    with pytest.raises(SystemExit):
        main(["--color", "sometimes", "x.c"])


@pytest.mark.parametrize(
    ("size", "env", "expected"),
    [
        (120, {}, 120),
        (0, {"COLUMNS": "90"}, 90),
        (0, {}, 80),
        (0, {"COLUMNS": "wide"}, 80),
    ],
)
def test_terminal_width_falls_back_when_the_terminal_reports_none(monkeypatch, size, env, expected):
    import os

    monkeypatch.delenv("COLUMNS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((size, 24)))
    stream = Stream()
    stream.fileno = lambda: 2  # type: ignore[method-assign]
    assert Progress(stream)._terminal_columns() == expected


def test_a_stream_without_a_terminal_uses_the_fallback_width(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    assert Progress(Stream())._terminal_columns() == 80  # fileno() raises OSError


# --- the pure text builders -------------------------------------------------------------


def _snapshot(**changes):
    fields = {
        "phase": "analyzing",
        "found": 10,
        "total": 10,
        "done": 4,
        "failed": 0,
        "findings": 0,
        "elapsed": 20.0,
        "analysis_elapsed": 20.0,
        "current": None,
        "running": 0.0,
        "done_bytes": 0,
        "total_bytes": 0,
    }
    fields.update(changes)
    return Snapshot(**fields)


def test_eta_is_count_based_without_sizes_and_size_based_with_them():
    assert eta_seconds(_snapshot()) == pytest.approx(30.0)  # 4 of 10 in 20s
    weighted = _snapshot(done_bytes=900, total_bytes=1000)
    assert eta_seconds(weighted) == pytest.approx(20 * 0.1 / 0.9)


@pytest.mark.parametrize(
    "changes",
    [
        {"total": 0},
        {"done": 0},
        {"done": 10},
        {"analysis_elapsed": 2.9},
        {"done": 1, "total": 100},  # under 5%
    ],
)
def test_eta_is_withheld_until_it_means_something(changes):
    assert eta_seconds(_snapshot(**changes)) is None


def test_status_text_orders_its_pieces_and_pluralizes():
    line = status_text(
        _snapshot(findings=1234, failed=2, current=Path("src/a.c")),
        Style(unicode=True),
        columns=120,
    )
    assert line == "4/10  ▕████░░░░░░▏   40%  ~30s  1,234 found  2 failed  " + str(Path("src/a.c"))


def test_status_text_never_exceeds_the_terminal_width():
    snapshot = _snapshot(findings=99999, current=Path("x/" * 80 + "file.c"))
    for columns in (30, 50, 80, 200):
        assert len(strip_ansi(status_text(snapshot, Style(), columns))) <= max(20, columns - 1)


def test_text_builders_for_each_phase():
    scanning = _snapshot(phase="scanning", found=1204, elapsed=65.0, total=0)
    assert status_text(scanning, Style(), 80) == "scanning... 1,204 files  1m05s"
    assert line_text(scanning) == "scanning, 1,204 files found, 1m05s"
    assert interrupted_text(scanning) == "interrupted while scanning (1,204 files found)"
    running = _snapshot(findings=1, failed=1)
    assert line_text(running) == "4/10 files (40%), 1 finding, 1 failed, 20s elapsed"
    assert summary_text(running) == "4 files in 20.0s, 1 finding, 1 failed"
    assert interrupted_text(running) == "interrupted after 4/10 files"


def test_plural_and_strip_ansi():
    assert (plural(1, "file"), plural(2, "file"), plural(12345, "file")) == (
        "1 file",
        "2 files",
        "12,345 files",
    )
    assert strip_ansi("\033[1;31mred\033[0m plain") == "red plain"


def test_simplify_macros_does_not_report_a_rewrite_that_failed_to_write(
    tmp_path, capsys, monkeypatch
):
    import builtins

    path = tmp_path / "a.c"
    path.write_text("#define M (A && A)\n", encoding="utf-8")
    real_open = builtins.open

    def failing_open(file, mode="r", *args, **kwargs):
        if "w" in mode and str(file) == str(path):
            raise PermissionError("read-only file")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    code, out, err = _run(capsys, "simplify-macros", "--rewrite", str(path))
    assert code != 0
    assert "REWRITTEN" not in out
    assert "read-only file" in err
    assert path.read_text(encoding="utf-8") == "#define M (A && A)\n"
