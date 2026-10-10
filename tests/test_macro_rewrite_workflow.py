"""Tests for user-facing macro simplification report and rewrite workflow (issue #80)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import cpre
from cpre.cli import main


def test_simplify_macros_cli_report_only_does_not_modify_source(tmp_path, capsys):
    source = tmp_path / "test.c"
    original = "#define FEAT_1 (0 && A) || B\n#define BUFFER_SIZE 1024\n"
    source.write_text(original, encoding="utf-8")

    code = main(["simplify-macros", str(source)])
    assert code == 0
    captured = capsys.readouterr()

    # Source file on disk MUST NOT be modified
    assert source.read_text(encoding="utf-8") == original

    # Report distinguishes original, simplified, proof/equivalence status, and location
    assert "line 1: #define FEAT_1 (0 && A) || B -> (B) [proven equivalent]" in captured.out
    assert "BUFFER_SIZE" not in captured.out


def test_analyze_macros_cli_alias_behavior(tmp_path, capsys):
    source = tmp_path / "test.c"
    original = "#define FEAT_1 (A && A)\n"
    source.write_text(original, encoding="utf-8")

    code = main(["analyze-macros", str(source)])
    assert code == 0
    captured = capsys.readouterr()
    assert source.read_text(encoding="utf-8") == original
    assert "line 1: #define FEAT_1 (A && A) -> (A) [proven equivalent]" in captured.out


def test_simplify_macros_cli_rewrite_in_place(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n#define BUFFER_SIZE 1024\n", encoding="utf-8")

    code = main(["simplify-macros", "--rewrite", str(source)])
    assert code == 0
    captured = capsys.readouterr()

    # Reporting output reflects rewrite status
    assert (
        "line 1: #define FEAT_1 (0 && A) || B -> (B) [rewritten, proven equivalent]" in captured.out
    )

    # Source was rewritten in-place
    rewritten = source.read_text(encoding="utf-8")
    assert "#define FEAT_1 (B)\n" in rewritten
    assert "#define BUFFER_SIZE 1024\n" in rewritten

    # Verification: re-analyzing rewritten source finds no simplifiable macros
    res = cpre.simplify_macros(rewritten)
    assert not res.has_findings
    assert len(res.simplifications) == 0


def test_simplify_macros_cli_in_place_flag_alias(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define M (A && A)\n", encoding="utf-8")

    code = main(["simplify-macros", "--in-place", str(source)])
    assert code == 0
    assert source.read_text(encoding="utf-8") == "#define M (A)\n"


def test_simplify_macros_cli_symbolic_zero_report_and_rewrite(tmp_path, capsys):
    source = tmp_path / "test.c"
    content = "#define FEAT_1 (0 && A) || B\n#define FEAT_2 (0 && A) || (0 && B)\n"
    source.write_text(content, encoding="utf-8")

    # In report mode with --symbolic-zero:
    # FEAT_1 is already simplest under symbolic-zero; only FEAT_2 simplifies to (0 && (A || B))
    code = main(["simplify-macros", "--symbolic-zero", str(source)])
    assert code == 0
    captured = capsys.readouterr()
    assert "FEAT_1" not in captured.out
    assert (
        "#define FEAT_2 (0 && A) || (0 && B) -> (0 && (A || B)) [proven equivalent]" in captured.out
    )
    assert source.read_text(encoding="utf-8") == content

    # Now rewrite with --symbolic-zero
    code = main(["simplify-macros", "--rewrite", "--symbolic-zero", str(source)])
    assert code == 0
    rewritten = source.read_text(encoding="utf-8")
    assert "#define FEAT_1 (0 && A) || B\n" in rewritten
    assert "#define FEAT_2 (0 && (A || B))\n" in rewritten


def test_simplify_macros_formatting_preservation_comments_indentation(tmp_path):
    source = tmp_path / "test.c"
    original = (
        "/* Header comment */\n"
        "#include <stdbool.h>\n"
        "\n"
        "    #define FOO   (A && A) /* inline comment */\n"
        "#define BAR (B || 0) // C++ comment\n"
        "\n"
        "int main() { return 0; }\n"
    )
    source.write_text(original, encoding="utf-8")

    code = main(["simplify-macros", "--rewrite", str(source)])
    assert code == 0

    rewritten = source.read_text(encoding="utf-8")
    expected = (
        "/* Header comment */\n"
        "#include <stdbool.h>\n"
        "\n"
        "    #define FOO   (A) /* inline comment */\n"
        "#define BAR (B) // C++ comment\n"
        "\n"
        "int main() { return 0; }\n"
    )
    assert rewritten == expected


def test_simplify_macros_formatting_preservation_continuations(tmp_path):
    source = tmp_path / "test.c"
    original = "#define FLAG1 \\\n    (A && A)\n#define FLAG2 (A && \\\n               A)\n"
    source.write_text(original, encoding="utf-8")

    code = main(["simplify-macros", "--rewrite", str(source)])
    assert code == 0

    rewritten = source.read_text(encoding="utf-8")
    expected = "#define FLAG1 \\\n    (A)\n#define FLAG2 (A)\n"
    assert rewritten == expected


def test_simplify_macros_preserves_crlf_line_endings(tmp_path):
    source = tmp_path / "test.c"
    original_bytes = b"#define M1 (A && A)\r\n#define M2 (B || 0)\r\n"
    source.write_bytes(original_bytes)

    code = main(["simplify-macros", "--rewrite", str(source)])
    assert code == 0

    rewritten_bytes = source.read_bytes()
    expected_bytes = b"#define M1 (A)\r\n#define M2 (B)\r\n"
    assert rewritten_bytes == expected_bytes


def test_simplify_macros_multiple_in_one_file(tmp_path):
    source = tmp_path / "test.c"
    original = (
        "#define M1 (A && A)\n#define M2 (B || 0)\n#define M3 (C && 1)\n#define M4 (D || !D)\n"
    )
    source.write_text(original, encoding="utf-8")

    code = main(["simplify-macros", "--rewrite", str(source)])
    assert code == 0

    rewritten = source.read_text(encoding="utf-8")
    expected = "#define M1 (A)\n#define M2 (B)\n#define M3 (C)\n#define M4 (1)\n"
    assert rewritten == expected


def test_simplify_macros_cli_json_single_file(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n", encoding="utf-8")

    # Report mode JSON
    code = main(["simplify-macros", "--json", str(source)])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(source)
    assert data["rewritten"] is False
    assert data["applied_count"] == 0
    assert data["verified"] is True
    assert data["semantics"] == "ordinary"
    assert data["symbolic_literals"] == []
    assert len(data["macros"]) == 1
    assert data["macros"][0]["name"] == "FEAT_1"
    assert data["macros"][0]["replacement"] == "(B)"
    assert data["macros"][0]["equivalent"] is True

    # Rewrite mode JSON
    code = main(["simplify-macros", "--rewrite", "--json", str(source)])
    assert code == 0
    data_rw = json.loads(capsys.readouterr().out)
    assert data_rw["rewritten"] is True
    assert data_rw["applied_count"] == 1
    assert data_rw["verified"] is True


def test_simplify_macros_cli_json_batch(tmp_path, capsys):
    f1 = tmp_path / "f1.c"
    f2 = tmp_path / "f2.c"
    f1.write_text("#define M1 (A && A)\n", encoding="utf-8")
    f2.write_text("#define M2 1024\n", encoding="utf-8")

    code = main(["simplify-macros", "--json", str(f1), str(f2)])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "files" in data
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == str(f1)


def test_simplify_macros_cli_batch_text(tmp_path, capsys):
    sub = tmp_path / "src"
    sub.mkdir()
    f1 = sub / "f1.c"
    f2 = sub / "f2.h"
    f1.write_text("#define M1 (A && A)\n", encoding="utf-8")
    f2.write_text("#define M2 (B || 0)\n", encoding="utf-8")

    code = main(["simplify-macros", "--recursive", str(sub)])
    assert code == 0
    captured = capsys.readouterr()
    assert "f1.c" in captured.out
    assert "#define M1 (A && A) -> (A) [proven equivalent]" in captured.out
    assert "f2.h" in captured.out
    assert "#define M2 (B || 0) -> (B) [proven equivalent]" in captured.out


def test_simplify_macros_cli_fail_on_findings(tmp_path):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT_1 (0 && A) || B\n", encoding="utf-8")

    code = main(["simplify-macros", "--fail-on-findings", str(source)])
    assert code == 1

    clean_source = tmp_path / "clean.c"
    clean_source.write_text("#define FEAT_2 (A || B)\n", encoding="utf-8")
    code_clean = main(["simplify-macros", "--fail-on-findings", str(clean_source)])
    assert code_clean == 0


def test_simplify_macros_cli_verbose_shows_skipped_and_unchanged(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text(
        "#define SIMPLIFIABLE (A && A)\n#define UNCHANGED (A || B)\n#define SKIPPED 1024\n",
        encoding="utf-8",
    )

    code = main(["simplify-macros", "--verbose", str(source)])
    assert code == 0
    captured = capsys.readouterr()
    assert "#define SIMPLIFIABLE (A && A) -> (A) [proven equivalent]" in captured.out
    assert (
        "#define UNCHANGED (A || B) (expression is already in simplest equivalent form)"
        in captured.out
    )
    assert "#define SKIPPED skipped: non-Boolean integer literal: '1024'" in captured.out


def test_python_api_simplify_macros_report_and_rewrite():
    source = (
        "#define FEAT_1 (0 && A) || B\n"
        "#define BUFFER_SIZE 1024\n"
        "#define FEAT_2 ((A && B) || (A && !B))\n"
    )

    # Report mode
    res = cpre.simplify_macros(source, filename="feature.c")
    assert isinstance(res, cpre.MacroSimplificationResult)
    assert res.source == source
    assert res.rewritten_source == source
    assert res.rewritten is False
    assert res.applied_count == 0
    assert res.verified is True
    assert res.has_findings is True
    assert len(res.simplifications) == 2

    # SuggestedEdit property on individual results
    simps = res.simplifications
    assert simps[0].name == "FEAT_1"
    assert simps[0].edit == cpre.SuggestedEdit(
        range=simps[0].replacement_range,
        replacement="(B)",
        confidence=cpre.FixConfidence.EXACT,
    )
    assert simps[1].name == "FEAT_2"
    assert simps[1].edit == cpre.SuggestedEdit(
        range=simps[1].replacement_range,
        replacement="(A)",
        confidence=cpre.FixConfidence.EXACT,
    )

    # Rewrite mode via simplify_macros(..., rewrite=True)
    rw_res = cpre.simplify_macros(source, rewrite=True, filename="feature.c")
    assert rw_res.rewritten is True
    assert rw_res.applied_count == 2
    assert rw_res.verified is True
    assert "#define FEAT_1 (B)\n" in rw_res.rewritten_source
    assert "#define BUFFER_SIZE 1024\n" in rw_res.rewritten_source
    assert "#define FEAT_2 (A)\n" in rw_res.rewritten_source

    # Convenience rewrite_macros
    rewritten_str = cpre.rewrite_macros(source)
    assert rewritten_str == rw_res.rewritten_source


def test_python_api_rewrite_macros_symbolic_zero():
    source = "#define FEAT_1 (0 && A) || B\n#define FEAT_2 (0 && A) || (0 && B)\n"

    # Ordinary semantics
    ord_rw = cpre.rewrite_macros(source)
    assert ord_rw == "#define FEAT_1 (B)\n#define FEAT_2 (0)\n"

    # Symbolic zero semantics
    sym_rw = cpre.rewrite_macros(source, symbolic_literals=(0,))
    assert sym_rw == "#define FEAT_1 (0 && A) || B\n#define FEAT_2 (0 && (A || B))\n"


def test_backward_compatibility_unified_cpre_cli(tmp_path, capsys):
    source = tmp_path / "test.c"
    source.write_text(
        "#if A || B\n#elif A\n#endif\n#define FEAT_1 (0 && A) || B\n", encoding="utf-8"
    )

    # Unified cpre analysis remains completely backward compatible
    code = main([str(source)])
    assert code == 0
    captured = capsys.readouterr()
    assert "#elif A [dead]" in captured.out
    assert "#define FEAT_1 (0 && A) || B -> (B) [proven equivalent]" in captured.out


def test_resource_limit_exceeded_macro_never_rewritten(tmp_path):
    source = tmp_path / "test.c"
    source.write_text("#define FEAT (A && B && C)\n", encoding="utf-8")
    opts = cpre.AnalysisOptions(max_atoms=1)
    res = cpre.simplify_macros(source.read_text(encoding="utf-8"), rewrite=True, options=opts)
    assert res.rewritten is False
    assert res.applied_count == 0
    assert res.results[0].incomplete is not None
    assert res.results[0].edit is None
    assert res.results[0].simplified is False


def test_simplify_macros_empty_or_no_macros(tmp_path, capsys):
    empty = tmp_path / "empty.c"
    empty.write_text("int x = 42;\n", encoding="utf-8")

    code = main(["simplify-macros", str(empty)])
    assert code == 0
    captured = capsys.readouterr()
    assert "No macro definitions found." in captured.out

    no_simp = tmp_path / "no_simp.c"
    no_simp.write_text("#define M (A || B)\n", encoding="utf-8")
    code = main(["simplify-macros", str(no_simp)])
    assert code == 0
    captured = capsys.readouterr()
    assert "No simplifiable macro definitions found." in captured.out


def test_simplify_macros_cli_help(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["simplify-macros", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage: cpre simplify-macros" in out
    assert "--rewrite" in out

    with pytest.raises(SystemExit) as excinfo2:
        main(["analyze-macros", "--help"])
    assert excinfo2.value.code == 0
    out2 = capsys.readouterr().out
    assert "usage: cpre analyze-macros" in out2


def test_reparsing_reanalyzing_verification_rigorous():
    source = "#define M1 (A && (A || B))\n#define M2 ((A && B) || (A && !B))\n#define M3 (!(!A))\n"
    # Simplify and rewrite
    res = cpre.simplify_macros(source, rewrite=True)
    assert res.rewritten is True
    assert res.applied_count == 3
    assert res.verified is True

    # Rigorous verification: parse and analyze rewritten source independently
    re_results = cpre.analyze_macros(res.rewritten_source)
    assert len(re_results) == 3
    for r in re_results:
        # Every rewritten macro must now be in simplest form (not simplifiable)
        assert r.simplified is False
        assert r.complete is True
        assert r.incomplete is None


def test_rewrite_macros_duplicate_macro_names_verified_by_index():
    source = "#define FOO (A && A)\n#undef FOO\n#define FOO (B || 0)\n"
    rewritten = cpre.rewrite_macros(source)
    assert rewritten == ("#define FOO (A)\n#undef FOO\n#define FOO (B)\n")


def test_rewrite_verification_length_mismatch(monkeypatch):
    source = "#define FOO (A && A)\n"
    results = cpre.analyze_macros(source)

    # Mock analyze_macros on re-analysis to return an empty tuple (length mismatch)
    monkeypatch.setattr(
        "cpre.macro_analysis.analyze_macros",
        lambda *args, **kwargs: (),
    )

    with pytest.raises(cpre.AnalysisError) as excinfo:
        cpre.macro_analysis._apply_and_verify_rewrites(
            source,
            results,
            symbolic_literals=(),
            options=None,
            filename=None,
        )
    assert "Rewrite verification failed: expected 1 macros after rewrite, found 0" in str(
        excinfo.value
    )
    assert excinfo.value.code == cpre.ErrorCode.ANALYSIS_FAILURE


def test_rewrite_verification_name_mismatch(monkeypatch):
    source = "#define FOO (A && A)\n"
    results = cpre.analyze_macros(source)
    assert len(results) == 1

    # Return a macro result with a mismatched name
    fake_re_result = cpre.MacroAnalysisResult(
        name="BAR",
        definition=results[0].definition,
        candidate=True,
    )
    monkeypatch.setattr(
        "cpre.macro_analysis.analyze_macros",
        lambda *args, **kwargs: (fake_re_result,),
    )

    with pytest.raises(cpre.AnalysisError) as excinfo:
        cpre.macro_analysis._apply_and_verify_rewrites(
            source,
            results,
            symbolic_literals=(),
            options=None,
            filename=None,
        )
    assert "Rewrite verification failed: macro 'FOO' not found after rewrite" in str(excinfo.value)
    assert excinfo.value.code == cpre.ErrorCode.ANALYSIS_FAILURE


def test_rewrite_verification_non_equivalent_expression(monkeypatch):
    source = "#define FOO (A && A)\n"
    results = cpre.analyze_macros(source)
    assert len(results) == 1
    assert results[0].simplified_replacement is not None

    # Return a complete macro result matching the expected name and replacement,
    # but with a semantically non-equivalent Boolean expression.
    fake_re_result = cpre.MacroAnalysisResult(
        name="FOO",
        definition=results[0].definition,
        candidate=True,
        original_replacement=results[0].simplified_replacement,
        original_expression=cpre.Variable(name="B"),
    )
    monkeypatch.setattr(
        "cpre.macro_analysis.analyze_macros",
        lambda *args, **kwargs: (fake_re_result,),
    )

    with pytest.raises(cpre.AnalysisError) as excinfo:
        cpre.macro_analysis._apply_and_verify_rewrites(
            source,
            results,
            symbolic_literals=(),
            options=None,
            filename=None,
        )
    assert (
        "Rewrite verification failed: 'FOO' rewritten expression is not equivalent to original"
        in str(excinfo.value)
    )
    assert excinfo.value.code == cpre.ErrorCode.ANALYSIS_FAILURE


# --diff and --check (issue #96)


def _rewrite_copy(tmp_path, name, text):
    """Rewrite a copy of ``text`` with --rewrite and return the result.

    Files are written and read as bytes throughout, so Windows newline
    translation cannot change the line endings under test.
    """
    copy = tmp_path / "rewritten" / name
    copy.parent.mkdir(exist_ok=True)
    copy.write_bytes(text.encode("utf-8"))
    assert main(["simplify-macros", "--rewrite", str(copy)]) == 0
    return copy.read_bytes().decode("utf-8")


def _apply_unified_diff(original, diff):
    """Apply a single-file unified diff to ``original`` (an independent check of --diff)."""
    old = original.split("\n")
    old = [line + "\n" for line in old[:-1]] + ([old[-1]] if old[-1] else [])
    out, position, last = [], 0, None
    diff_lines = [line + "\n" for line in diff.split("\n")[:-1]]
    for line in diff_lines[2:]:
        if line.startswith("@@ "):
            start = int(line.split()[1].split(",")[0][1:])
            hunk = max(start - 1, 0)
            out.extend(old[position:hunk])
            position = hunk
        elif line.startswith("\\"):
            # The previous diff line had no newline at end of file.
            if last in (" ", "+"):
                out[-1] = out[-1][:-1]
        elif line[0] == " ":
            assert old[position].rstrip("\r\n") == line[1:].rstrip("\r\n")
            out.append(old[position])
            position += 1
        elif line[0] == "-":
            assert old[position].rstrip("\r\n") == line[1:].rstrip("\r\n")
            position += 1
        else:
            out.append(line[1:])
        last = line[0]
    out.extend(old[position:])
    return "".join(out)


def test_simplify_macros_diff_matches_rewrite_and_leaves_files_untouched(tmp_path, capsys):
    source = tmp_path / "test.c"
    original = "#define FEAT_1 (0 && A) || B\nint x;\n#define FEAT_2 (C && C)\n#define N 1024\n"
    source.write_bytes(original.encode("utf-8"))

    code = main(["simplify-macros", "--diff", str(source)])
    captured = capsys.readouterr()

    assert code == 0
    assert source.read_bytes() == original.encode("utf-8")
    assert captured.err == ""
    assert captured.out == (
        f"--- {source}\n"
        f"+++ {source}\n"
        "@@ -1,4 +1,4 @@\n"
        "-#define FEAT_1 (0 && A) || B\n"
        "+#define FEAT_1 (B)\n"
        " int x;\n"
        "-#define FEAT_2 (C && C)\n"
        "+#define FEAT_2 (C)\n"
        " #define N 1024\n"
    )
    rewritten = _rewrite_copy(tmp_path, "test.c", original)
    assert _apply_unified_diff(original, captured.out) == rewritten


@pytest.mark.parametrize(
    "original",
    [
        "#define W (X || X)\r\nint y;\r\n",
        "#define W (X || X)",
    ],
    ids=["crlf", "no-final-newline"],
)
def test_simplify_macros_diff_preserves_line_endings(tmp_path, capsys, original):
    source = tmp_path / "test.c"
    source.write_bytes(original.encode("utf-8"))

    assert main(["simplify-macros", "--diff", str(source)]) == 0
    diff = capsys.readouterr().out

    assert source.read_bytes() == original.encode("utf-8")
    if original.endswith("\n"):
        assert "No newline" not in diff
    else:
        assert diff.endswith("\n\\ No newline at end of file\n")
    assert _apply_unified_diff(original, diff) == _rewrite_copy(tmp_path, "test.c", original)


def test_simplify_macros_diff_stdout_is_byte_exact(tmp_path):
    # Text-mode stdout on Windows would turn the CRLF diff lines into CR CR LF.
    source = tmp_path / "test.c"
    original = b"#define W (X || X)\r\nint y;\r\n"
    source.write_bytes(original)

    completed = subprocess.run(
        [sys.executable, "-m", "cpre", "simplify-macros", "--diff", str(source)],
        capture_output=True,
        check=True,
    )

    assert completed.stdout == (
        f"--- {source}\n+++ {source}\n@@ -1,2 +1,2 @@\n".encode()
        + b"-#define W (X || X)\r\n+#define W (X)\r\n int y;\r\n"
    )


def test_unified_diff_splits_lines_on_newline_only():
    from cpre.cli import _unified_diff

    # str.splitlines() would also split on the form feed and the lone CR.
    before = "\f\nint y;\r\f\n#define W (X || X)\n"
    after = "\f\nint y;\r\f\n#define W (X)\n"

    diff = _unified_diff(Path("w.c"), before, after)

    assert diff == (
        "--- w.c\n+++ w.c\n@@ -1,3 +1,3 @@\n \f\n int y;\r\f\n-#define W (X || X)\n+#define W (X)\n"
    )
    assert _apply_unified_diff(before, diff) == after


def test_simplify_macros_diff_and_check_without_changes(tmp_path, capsys):
    source = tmp_path / "clean.c"
    original = "#define FEAT (A || B)\n#define N 1024\n"
    source.write_bytes(original.encode("utf-8"))

    assert main(["simplify-macros", "--diff", str(source)]) == 0
    assert main(["simplify-macros", "--check", str(source)]) == 0
    assert main(["simplify-macros", "--check", "--diff", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert source.read_bytes() == original.encode("utf-8")


def test_simplify_macros_check_names_files_that_would_change(tmp_path, capsys):
    changed_a = tmp_path / "a.c"
    clean = tmp_path / "b.c"
    changed_c = tmp_path / "c.c"
    changed_a.write_bytes(b"#define A1 (A && A)\n")
    clean.write_bytes(b"#define B1 B\n")
    changed_c.write_bytes(b"#define C1 (0 || C)\n#define C2 (D && 1)\n")

    code = main(["simplify-macros", "--check", str(tmp_path)] + ["--recursive"])
    captured = capsys.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == f"would rewrite {changed_a}\nwould rewrite {changed_c}\n"
    assert changed_a.read_bytes() == b"#define A1 (A && A)\n"


def test_simplify_macros_check_with_diff_prints_every_changed_file(tmp_path, capsys):
    first = tmp_path / "a.c"
    second = tmp_path / "b.c"
    first.write_bytes(b"#define A1 (A && A)\n")
    second.write_bytes(b"#define B1 (B || B)\n")

    code = main(["simplify-macros", "--check", "--diff", str(first), str(second)])
    captured = capsys.readouterr()

    assert code == 1
    assert captured.out == (
        f"--- {first}\n+++ {first}\n@@ -1 +1 @@\n-#define A1 (A && A)\n+#define A1 (A)\n"
        f"--- {second}\n+++ {second}\n@@ -1 +1 @@\n-#define B1 (B || B)\n+#define B1 (B)\n"
    )
    assert captured.err == f"would rewrite {first}\nwould rewrite {second}\n"


def test_simplify_macros_check_respects_symbolic_literals(tmp_path, capsys):
    source = tmp_path / "switch.c"
    source.write_bytes(b"#define FEAT (0 && A) || B\n")

    # Ordinary semantics fold the disabled switch away; symbolic-zero keeps it.
    assert main(["simplify-macros", "--check", str(source)]) == 1
    assert main(["simplify-macros", "--check", "--symbolic-zero", str(source)]) == 0
    capsys.readouterr()


def test_simplify_macros_check_errors_exit_2(tmp_path, capsys):
    changed = tmp_path / "a.c"
    changed.write_bytes(b"#define A1 (A && A)\n")
    unreadable = tmp_path / "b.c"
    unreadable.write_bytes(b"#define B1 \xff\n")

    code = main(["simplify-macros", "--check", "--diff", str(changed), str(unreadable)])
    captured = capsys.readouterr()

    # Errors take precedence over "would rewrite"; readable files are still reported.
    assert code == 2
    assert f"--- {changed}" in captured.out
    assert f"would rewrite {changed}" in captured.err
    assert f"{unreadable}: " in captured.err


@pytest.mark.parametrize(
    "flags",
    [
        ["--rewrite", "--diff"],
        ["--in-place", "--check"],
        ["--json", "--diff"],
        ["--json", "--check"],
    ],
)
def test_simplify_macros_preview_flags_reject_conflicts(tmp_path, capsys, flags):
    source = tmp_path / "test.c"
    original = "#define A1 (A && A)\n"
    source.write_bytes(original.encode("utf-8"))

    with pytest.raises(SystemExit) as caught:
        main(["simplify-macros", *flags, str(source)])

    assert caught.value.code == 2
    assert "cannot be combined with --diff or --check" in capsys.readouterr().err
    assert source.read_bytes() == original.encode("utf-8")
