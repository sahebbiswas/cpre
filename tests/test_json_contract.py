"""The documented ``--json`` contract (#102): schema ids, field sets, ordering, determinism."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cpre
from cpre import serialization
from cpre.cli import main

SOURCE = (
    "#define A (X || X)\n#define B (Y && Y)\n#if X\n#if !X\nint dead;\n#endif\n#endif\n"
    "#if P && Q\nint p;\n#endif\n"
)

FILE_KEYS = {
    "schema", "path", "groups", "complete", "partial", "incomplete", "incomplete_components",
    "macros", "semantics", "symbolic_literals",
}  # fmt: skip
SIMPLIFY_KEYS = {
    "schema", "path", "rewritten", "applied_count", "verified", "semantics", "symbolic_literals",
    "macros",
}  # fmt: skip
PREVIEW_KEYS = {
    "schema", "path", "would_change", "applied_count", "verified", "diff", "semantics",
    "symbolic_literals",
}  # fmt: skip
PREPROCESS_KEYS = {"schema", "file", "complete", "source", "diagnostics", "skipped_includes"}
UNKNOWN_KEYS = {"schema", "file", "unknown_macros"}
EXPRESSION_KEYS = {
    "schema", "input", "simplified", "equivalent", "changed", "semantics", "symbolic_literals",
}  # fmt: skip
MACRO_KEYS = {
    "name", "line", "column", "candidate", "simplified", "original", "replacement", "equivalent",
    "rewritable", "reason", "semantics", "symbolic_literals",
}  # fmt: skip
DIAGNOSTIC_KEYS = {"code", "message", "line", "source_identity", "condition", "unresolved"}
INCOMPLETE_KEYS = {"code", "resource", "limit", "observed", "message", "line", "component"}
UNRESOLVED_KEYS = {"name", "uses", "lines", "suggestions"}


def _run(capsys, argv):
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "a.c"
    path.write_bytes(SOURCE.encode())
    return path


def test_schema_ids_name_the_document_and_major_version():
    assert serialization.SCHEMA_VERSION == 1
    assert serialization.schema_id("analysis") == "cpre.analysis/1"
    document = serialization.top_level("analysis", {"path": "x"})
    assert list(document) == ["schema", "path"]
    batch = serialization.batch_document("analysis", [{"path": "x"}])
    assert batch == {"schema": "cpre.analysis/1", "files": [{"path": "x"}]}


def test_analysis_document_fields(source, capsys):
    _, data = _run(capsys, ["--json", str(source)])
    assert set(data) == FILE_KEYS and data["schema"] == "cpre.analysis/1"
    assert data["complete"] is True and data["partial"] is False
    assert data["incomplete"] == [] and data["incomplete_components"] == []
    assert [m["name"] for m in data["macros"]] == ["A", "B"]
    assert all(set(m) == MACRO_KEYS for m in data["macros"])


def test_batch_documents_carry_one_schema_and_no_repeats(source, tmp_path, capsys):
    other = tmp_path / "b.c"
    other.write_bytes(SOURCE.encode())
    _, data = _run(capsys, ["--json", "--verbose", str(source), str(other)])
    assert set(data) == {"schema", "files"} and data["schema"] == "cpre.analysis/1"
    assert [f["path"] for f in data["files"]] == [str(source), str(other)]
    assert all("schema" not in f and set(f) == FILE_KEYS - {"schema"} for f in data["files"])


def test_disabled_conditional_analysis_reports_unknown_status_not_complete(source, capsys):
    _, data = _run(capsys, ["--json", "--no-conditionals", str(source)])
    assert data["groups"] == []
    for key in ("complete", "partial", "incomplete", "incomplete_components"):
        assert data[key] is None


def test_incomplete_analysis_has_stable_diagnostics():
    analysis = cpre.analyze_source(
        "#if A && B\n#endif\n", options=cpre.AnalysisOptions(max_atoms=1)
    )
    status = serialization.analysis_status_dict(analysis)
    assert status["complete"] is False
    assert all(set(d) == INCOMPLETE_KEYS for d in status["incomplete"])
    component = status["incomplete_components"][0]
    assert set(component) == {"index", "groups", "atoms", "diagnostics"}
    assert set(component["groups"][0]) == {"line", "end_line", "directive", "condition"}
    text = json.dumps(status)
    assert "node" not in text.lower() and "bdd" not in text.lower()


def test_simplify_macros_document_fields(source, capsys):
    _, data = _run(capsys, ["simplify-macros", "--json", str(source)])
    assert set(data) == SIMPLIFY_KEYS and data["schema"] == "cpre.simplify-macros/1"
    assert [m["name"] for m in data["macros"]] == ["A", "B"]


def test_simplify_macros_preview_document_fields(source, capsys):
    _, data = _run(capsys, ["simplify-macros", "--json", "--check", "--diff", str(source)])
    assert set(data) == PREVIEW_KEYS and data["schema"] == "cpre.simplify-macros-preview/1"
    assert "rewritten" not in data


def test_macro_environment_document_fields(source, capsys):
    _, data = _run(capsys, ["preprocess", "--dump-macros", "--json", "-D", "X", str(source)])
    assert set(data) == {"schema", "file", "unknown_names", "macros"}
    assert data["schema"] == "cpre.macro-environment/1"
    assert all(
        set(m) == {"name", "defined", "replacement", "parameters", "variadic", "origin", "path"}
        for m in data["macros"]
    )


def test_cover_document_fields(source, capsys):
    code = main(["cover", "--json", str(source)])
    data = json.loads(capsys.readouterr().out)
    assert code in (0, 1)
    assert set(data) == {"schema", "file", "complete", "configurations", "outcomes", "incomplete"}
    assert data["schema"] == "cpre.cover/1"


def test_preprocess_document_distinguishes_complete_incomplete_and_empty(source, capsys):
    _, incomplete = _run(capsys, ["preprocess", "--json", str(source)])
    assert set(incomplete) == PREPROCESS_KEYS and incomplete["schema"] == "cpre.preprocess/1"
    assert incomplete["complete"] is False and incomplete["source"] is None
    assert all(set(d) == DIAGNOSTIC_KEYS for d in incomplete["diagnostics"])
    assert all(
        set(u) == UNRESOLVED_KEYS for d in incomplete["diagnostics"] for u in d["unresolved"]
    )
    _, complete = _run(
        capsys, ["preprocess", "--json", "-D", "X=0", "-D", "P=0", "-D", "Y", str(source)]
    )
    assert complete["complete"] is True and isinstance(complete["source"], str)
    assert complete["diagnostics"] == [] and complete["skipped_includes"] == []


def test_unknown_macros_document_fields_and_order(source, capsys):
    _, data = _run(capsys, ["preprocess", "--list-unknown-macros", "--json", str(source)])
    assert set(data) == UNKNOWN_KEYS and data["schema"] == "cpre.unknown-macros/1"
    names = [m["name"] for m in data["unknown_macros"]]
    assert names == sorted(names) and {"X", "P", "Q"} <= set(names)
    assert all(set(m) == UNRESOLVED_KEYS for m in data["unknown_macros"])


def test_expression_document_fields(capsys):
    _, data = _run(capsys, ["test-input", "--json", "A && (A || B)"])
    assert set(data) == EXPRESSION_KEYS and data["schema"] == "cpre.expression/1"


EVALUATION_KEYS = {
    "schema", "kind", "expression", "state", "value", "unsigned", "expanded", "reason",
    "message", "offset",
}  # fmt: skip


def test_evaluation_document_fields_and_schema():
    import cpre
    from cpre import serialization

    config = cpre.MacroConfiguration(integers={"A": 2})
    for evaluation in (
        cpre.evaluate_expression("A * 3", config),
        cpre.evaluate_expression("A / 0", config),
        cpre.evaluate_macro("MISSING", config),
    ):
        data = serialization.evaluation_document(evaluation)
        assert list(data)[0] == "schema" and data["schema"] == "cpre.evaluation/1"
        assert set(data) == EVALUATION_KEYS


@pytest.mark.parametrize("seed", ["0", "1", "12345"])
def test_output_is_identical_across_hash_seeds(source, seed):
    def output(env_seed):
        env = {**os.environ, "PYTHONHASHSEED": env_seed}
        run = subprocess.run(
            [sys.executable, "-m", "cpre", "--json", "--verbose", str(source)],
            capture_output=True, text=True, env=env, check=True,
        )  # fmt: skip
        return run.stdout

    assert output(seed) == output("random")


def test_documented_contract_names_every_schema_kind():
    text = (Path(__file__).parent.parent / "docs" / "json-contract.md").read_text(encoding="utf-8")
    for kind in (
        "analysis",
        "simplify-macros",
        "simplify-macros-preview",
        "macro-environment",
        "cover",
        "preprocess",
        "unknown-macros",
        "expression",
        "evaluation",
    ):
        assert f"cpre.{kind}/1" in text
