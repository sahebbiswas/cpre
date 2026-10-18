"""``cpre.serialization`` and ``cpre.text_reports`` build output without printing or exiting."""

import json
from pathlib import Path

import cpre
from cpre import serialization, text_reports
from cpre.errors import SourceLocation
from cpre.macro_analysis import analyze_macros, simplify_macros
from cpre.pragmas import preprocess_source
from cpre.unknown_macros import unknown_macros_in_source

SOURCE = "#define A (X || X)\n#if X\n#if !X\nint dead;\n#endif\n#endif\n"


def test_incomplete_diagnostics_serialize_to_plain_data():
    options = cpre.AnalysisOptions(max_atoms=1)
    analysis = cpre.analyze_source("#if A && B\n#endif\n", options=options)
    assert not analysis.complete
    status = serialization.analysis_status_dict(analysis)
    assert status["complete"] is False
    json.dumps(status)
    first = serialization.incomplete_to_dict(analysis.incomplete[0])
    assert first["code"] == analysis.incomplete[0].code.value
    assert set(first) == {"code", "resource", "limit", "observed", "message", "line", "component"}


def test_file_json_has_groups_macros_and_semantics():
    analysis = cpre.analyze_source(SOURCE)
    macros = analyze_macros(SOURCE)
    document, has_visible = serialization.build_file_json(
        "f.c",
        analysis.tree,
        macros,
        verbose=False,
        enable_macros=True,
        symbolic_literals=(),
        analysis=analysis,
    )
    assert has_visible
    assert document["path"] == "f.c"
    assert document["semantics"] == "ordinary" and document["symbolic_literals"] == []
    assert [m["name"] for m in document["macros"]] == ["A"]
    json.dumps(document)


def test_file_json_without_trees_or_macros_is_empty_but_well_formed():
    document, has_visible = serialization.build_file_json(
        Path("f.c"), None, [], verbose=True, enable_macros=False, symbolic_literals=(0,)
    )
    assert not has_visible
    assert document == {
        "path": "f.c",
        "groups": [],
        "complete": None,
        "partial": None,
        "incomplete": None,
        "incomplete_components": None,
        "macros": [],
        "semantics": "symbolic-literal",
        "symbolic_literals": [0],
    }


def test_simplify_macros_json_reports_rewrite_state():
    result = simplify_macros(SOURCE, rewrite=True)
    document, has_visible = serialization.build_simplify_macros_json(
        "f.c", result, verbose=False, symbolic_literals=()
    )
    assert has_visible is result.has_findings
    assert document["rewritten"] is result.rewritten
    assert document["applied_count"] == result.applied_count


def test_preprocess_document_carries_diagnostics():
    result = preprocess_source("#if U\n#endif\n")
    document = serialization.preprocess_document("f.c", result, None)
    assert document["file"] == "f.c" and document["complete"] is False
    assert document["source"] is None and document["skipped_includes"] == []
    (diagnostic,) = document["diagnostics"]
    assert diagnostic["code"] == "unresolved_condition"
    assert diagnostic["unresolved"][0]["name"] == "U"
    json.dumps(document)


def test_preprocess_document_lists_skipped_includes():
    result = preprocess_source('#include "x.h"\nint a;\n', skip_includes=True)
    document = serialization.preprocess_document("f.c", result, result.source)
    assert document["complete"] is True and document["diagnostics"] == []
    (skipped,) = document["skipped_includes"]
    assert (skipped["directive"], skipped["operand"], skipped["line"]) == ("include", '"x.h"', 1)


def test_unknown_macros_document_and_hints():
    unknown = unknown_macros_in_source("#if defined(A)\n#endif\n#if B > 1\n#endif\n")
    document = serialization.unknown_macros_document("f.c", unknown)
    assert [item["name"] for item in document["unknown_macros"]] == ["A", "B"]
    hints = {item.name: text_reports.unknown_macro_hint(item) for item in unknown}
    assert hints["A"] == "supply -D A or -U A"
    assert hints["B"] == "supply -D B=<value>, or -U B to evaluate it as 0"


def test_expression_document_and_semantics_name():
    document = serialization.expression_document(
        "A && A", "A", equivalent=True, changed=True, symbolic_literals=(0, 1)
    )
    assert document["semantics"] == "symbolic-literal"
    assert document["symbolic_literals"] == [0, 1]
    assert serialization.semantics_name(()) == "ordinary"


def test_error_and_diagnostic_text():
    located = cpre.CpreError(
        "bad", code=cpre.ErrorCode.SOURCE_READ_ERROR, location=SourceLocation(3, 5)
    )
    assert text_reports.format_error(located) == "bad at line 3, column 5"
    assert (
        text_reports.format_error(cpre.CpreError("bad", code=cpre.ErrorCode.SOURCE_READ_ERROR))
        == "bad"
    )
    line_only = cpre.CpreError(
        "bad", code=cpre.ErrorCode.SOURCE_READ_ERROR, location=SourceLocation(3)
    )
    assert text_reports.format_error(line_only) == "line 3: bad"


def test_incomplete_text_names_each_conditional():
    options = cpre.AnalysisOptions(max_atoms=1)
    analysis = cpre.analyze_source("#if A && B\n#endif\n", options=options)
    (component,) = analysis.incomplete_components
    assert text_reports.format_incomplete_component(component).startswith("line 1: conditional at")
    lines = text_reports.incomplete_lines([component])
    assert lines[0].startswith("Incomplete analysis")
    assert lines[1].startswith("  1-2: #if A && B [incomplete:")
    assert text_reports.incomplete_lines([]) == []


def test_macro_and_combined_reports():
    macros = analyze_macros(SOURCE)
    report, has_entries = text_reports.render_macro_report(macros)
    assert has_entries and "#define A (X || X) -> (!!X) [proven equivalent]" in report
    assert text_reports.render_macro_report([]) == ("No macro definitions found.", False)
    plain = text_reports.render_macro_report(macros, color=True)[0]
    assert "\x1b[" in plain
    tree = cpre.analyze_source(SOURCE).tree
    combined, any_entry = text_reports.render_combined_report(tree, macros)
    assert any_entry and "#define A" in combined
    empty = text_reports.render_combined_report(None, [])
    assert empty == ("No conditional directives or macro definitions found.", False)


def test_unified_diff_is_patch_applicable():
    diff = text_reports.unified_diff(Path("f.c"), "a\nb\n", "a\nc\n")
    assert diff.startswith("--- f.c\n+++ f.c\n@@")
    assert "-b\n+c\n" in diff
    no_newline = text_reports.unified_diff(Path("f.c"), "a", "b")
    assert no_newline.count("\\ No newline at end of file") == 2
    assert text_reports.unified_diff(Path("f.c"), "same\n", "same\n") == ""
