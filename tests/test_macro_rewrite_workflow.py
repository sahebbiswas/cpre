"""Tests for user-facing macro simplification report and rewrite workflow (issue #80)."""

import json
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
    source = (
        "#define FOO (A && A)\n"
        "#undef FOO\n"
        "#define FOO (B || 0)\n"
    )
    rewritten = cpre.rewrite_macros(source)
    assert rewritten == (
        "#define FOO (A)\n"
        "#undef FOO\n"
        "#define FOO (B)\n"
    )


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
    assert "Rewrite verification failed: macro 'FOO' not found after rewrite" in str(
        excinfo.value
    )
    assert excinfo.value.code == cpre.ErrorCode.ANALYSIS_FAILURE

