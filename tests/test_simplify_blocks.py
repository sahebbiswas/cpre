"""``cpre simplify-blocks`` and ``rewrite_blocks``."""

from __future__ import annotations

import json

import pytest

from cpre import FindingKind
from cpre.block_rewrite import rewrite_blocks
from cpre.cli import main

PAIR = "#if A\nx;\n#endif\n#if A\ny;\n#endif\n"
MERGED = "#if A\nx;\ny;\n#endif\n"


def test_run_of_three_merges_into_one() -> None:
    source = "#if A\nx;\n#endif\n#if A\ny;\n#endif\n#if A\nz;\n#endif\n"
    result = rewrite_blocks(source)
    assert result.rewritten_source == "#if A\nx;\ny;\nz;\n#endif\n"
    assert not result.remaining


def test_crlf_and_missing_final_newline_are_kept() -> None:
    result = rewrite_blocks(PAIR.replace("\n", "\r\n"))
    assert result.rewritten_source == MERGED.replace("\n", "\r\n")
    result = rewrite_blocks(PAIR.rstrip("\n"))
    assert result.rewritten_source == MERGED.rstrip("\n")


def test_nested_rewrites_apply_over_rounds() -> None:
    source = "#if O\n#if A\nx;\n#endif\n#if A\ny;\n#endif\n#endif\n#if O\nz;\n#endif\n"
    result = rewrite_blocks(source)
    assert result.changed
    assert result.rewritten_source.count("#if") <= 2


def test_empty_block_removed() -> None:
    assert rewrite_blocks("#if A\n#endif\nx;\n").rewritten_source == "x;\n"


def test_nesting_only_when_asked() -> None:
    source = "#if A\n#if B\nx;\n#endif\n#endif\n"
    assert not rewrite_blocks(source).changed
    result = rewrite_blocks(source, kinds={FindingKind.COLLAPSIBLE_NESTING})
    assert result.changed
    assert "#if A && B" in result.rewritten_source or "#if (A) && (B)" in result.rewritten_source


def test_unknown_kind_rejected() -> None:
    from cpre import AnalysisError

    with pytest.raises(AnalysisError):
        rewrite_blocks(PAIR, kinds={FindingKind.DEAD_BRANCH})


def test_nothing_to_do(tmp_path) -> None:
    path = tmp_path / "a.c"
    path.write_text("#if A\nx;\n#endif\n")
    assert main(["simplify-blocks", "--check", str(path)]) == 0


def test_check_diff_rewrite(tmp_path, capsys) -> None:
    path = tmp_path / "a.c"
    path.write_bytes(PAIR.encode())
    assert main(["simplify-blocks", "--check", str(path)]) == 1
    assert f"would rewrite {path}" in capsys.readouterr().err
    assert path.read_text() == PAIR
    assert main(["simplify-blocks", "--diff", str(path)]) == 0
    assert "-#endif" in capsys.readouterr().out
    assert path.read_text() == PAIR
    assert main(["simplify-blocks", "--rewrite", str(path)]) == 0
    assert path.read_text() == MERGED
    assert main(["simplify-blocks", "--check", str(path)]) == 0


def test_crlf_file_preserved(tmp_path) -> None:
    path = tmp_path / "a.c"
    path.write_bytes(PAIR.replace("\n", "\r\n").encode())
    assert main(["simplify-blocks", "--rewrite", str(path)]) == 0
    assert path.read_bytes() == MERGED.replace("\n", "\r\n").encode()


def test_json(tmp_path, capsys) -> None:
    path = tmp_path / "a.c"
    path.write_text(PAIR)
    assert main(["simplify-blocks", "--json", "--diff", str(path)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == "cpre.simplify-blocks/1"
    assert document["would_change"] is True
    assert document["rewritten"] is False
    assert document["applied"][0]["kind"] == "mergeable_blocks"
    assert "diff" in document and document["diff"]


@pytest.mark.parametrize("flags", [["--rewrite", "--diff"], ["--rewrite", "--check"]])
def test_flag_conflicts(flags, tmp_path) -> None:
    path = tmp_path / "a.c"
    path.write_text(PAIR)
    with pytest.raises(SystemExit) as exit_info:
        main(["simplify-blocks", *flags, str(path)])
    assert exit_info.value.code == 2


def test_stdin_rewrite_rejected() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["simplify-blocks", "--rewrite", "-"])
    assert exit_info.value.code == 2


def test_verification_failure_leaves_file_alone(tmp_path, monkeypatch, capsys) -> None:
    import cpre.block_rewrite as module
    from cpre import AnalysisError
    from cpre.errors import ErrorCode

    def refuse(*args, **kwargs):
        raise AnalysisError("rewrite verification failed: test", code=ErrorCode.ANALYSIS_FAILURE)

    monkeypatch.setattr(module, "_verify", refuse)
    path = tmp_path / "a.c"
    path.write_text(PAIR)
    assert main(["simplify-blocks", "--rewrite", str(path)]) == 2
    assert path.read_text() == PAIR
    assert "verification failed" in capsys.readouterr().err


def test_a_merge_that_leaves_a_block_holding_one_block_is_not_a_verification_failure():
    source = "#if A\n#if B\nx;\n#endif\n#if B\ny;\n#endif\n#endif\n"
    result = rewrite_blocks(source)
    assert result.rewritten_source == "#if A\n#if B\nx;\ny;\n#endif\n#endif\n"
    source = "#if A\n#if B\nx;\n#endif\n#if C\n#endif\n#endif\n"
    assert rewrite_blocks(source).rewritten_source == "#if A\n#if B\nx;\n#endif\n#endif\n"


def test_the_cascade_is_applied_across_rounds_with_collapse_nesting():
    source = "#if A\n#if B\nx;\n#endif\n#if C\n#endif\n#endif\n"
    result = rewrite_blocks(
        source, kinds={FindingKind.EMPTY_BLOCK, FindingKind.COLLAPSIBLE_NESTING}
    )
    assert result.rewritten_source == "#if A && B\nx;\n#endif\n"
    assert result.rounds == 2


def test_without_a_mode_flag_the_report_says_what_would_apply(tmp_path, capsys):
    path = tmp_path / "a.c"
    path.write_text(PAIR)
    assert main(["simplify-blocks", str(path)]) == 0
    out = capsys.readouterr().out
    assert "would apply 1 edit(s): mergeable_blocks (line 1)" in out
    assert "--diff" in out
    assert path.read_text() == PAIR
    assert main(["simplify-blocks", "--fail-on-findings", str(path)]) == 1
    capsys.readouterr()
    path.write_text("#if A\nx;\n#endif\n")
    assert main(["simplify-blocks", str(path)]) == 0
    assert "No block edits to apply." in capsys.readouterr().out
