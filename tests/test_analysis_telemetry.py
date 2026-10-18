"""Opt-in resource telemetry for ``analyze_source`` and ``cpre --telemetry`` (#103)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import cpre
from cpre import AnalysisOptions, analyze_source
from cpre.cli import main

MULTI = "#if A && B\n#endif\n#if C\n#if D\n#endif\n#endif\n#if X\n#endif\n"


def test_telemetry_is_absent_unless_requested():
    assert analyze_source(MULTI).telemetry is None


def test_findings_are_identical_with_and_without_telemetry():
    source = "#if X\n#if !X\nint dead;\n#endif\n#endif\n#if A || A\n#endif\n"
    plain = analyze_source(source)
    traced = analyze_source(source, telemetry=True)
    assert traced.findings == plain.findings
    assert traced.incomplete == plain.incomplete
    assert traced.incomplete_components == plain.incomplete_components
    assert traced.tree is not plain.tree and traced.complete == plain.complete
    limited = AnalysisOptions(max_atoms=1)
    assert (
        analyze_source(MULTI, options=limited, telemetry=True).incomplete
        == analyze_source(MULTI, options=limited).incomplete
    )


def test_multi_component_figures():
    telemetry = analyze_source(MULTI, telemetry=True).telemetry
    assert telemetry is not None
    assert telemetry.atoms == 5
    assert [(c.atoms, c.groups, c.status) for c in telemetry.components] == [
        (2, 1, "analyzed"),
        (2, 1, "analyzed"),
        (1, 1, "analyzed"),
    ]
    assert all(c.nodes > 0 and c.resource is None for c in telemetry.components)
    assert telemetry.limits == AnalysisOptions()
    assert telemetry.work > 0


def test_atom_limit_marks_only_the_oversized_components_incomplete():
    result = analyze_source(MULTI, options=AnalysisOptions(max_atoms=1), telemetry=True)
    telemetry = result.telemetry
    assert telemetry is not None and not result.complete and result.partial
    statuses = [(c.status, c.resource, c.incomplete_component) for c in telemetry.components]
    assert statuses == [
        ("incomplete", "atoms", 0),
        ("incomplete", "atoms", 1),
        ("analyzed", None, None),
    ]
    assert telemetry.limits.max_atoms == 1
    for item in telemetry.components:
        if item.incomplete_component is not None:
            diagnostic = result.incomplete_components[item.incomplete_component].diagnostics[0]
            assert diagnostic.resource == item.resource


def test_node_limit_names_the_resource():
    source = "#if (A && B) || (C && D) || (E && F)\n#endif\n"
    result = analyze_source(source, options=AnalysisOptions(max_bdd_nodes=3), telemetry=True)
    assert not result.complete
    assert result.telemetry is not None
    assert [c.resource for c in result.telemetry.components] == ["bdd_nodes"]


def test_work_exhaustion_leaves_components_unfinished():
    result = analyze_source(MULTI, options=AnalysisOptions(max_work=3), telemetry=True)
    assert not result.complete and not result.partial
    telemetry = result.telemetry
    assert telemetry is not None
    assert telemetry.atoms == 5 and len(telemetry.components) == 3
    assert {c.status for c in telemetry.components} == {"unfinished"}
    assert telemetry.work == 4  # the limit plus the one unit that exceeded it


def test_work_exhaustion_keeps_finished_and_already_failed_components():
    finished = analyze_source(MULTI, options=AnalysisOptions(max_work=100), telemetry=True)
    assert not finished.complete and finished.telemetry is not None
    assert [c.status for c in finished.telemetry.components] == [
        "analyzed",
        "analyzed",
        "unfinished",
    ]
    failed = analyze_source(MULTI, options=AnalysisOptions(max_atoms=1, max_work=8), telemetry=True)
    assert failed.telemetry is not None and not failed.partial
    assert [(c.status, c.resource) for c in failed.telemetry.components] == [
        ("incomplete", "atoms"),
        ("incomplete", "atoms"),
        ("unfinished", None),
    ]
    # The curtailed result itself carries no component entries to point at.
    assert all(c.incomplete_component is None for c in failed.telemetry.components)


def test_assumptions_are_covered():
    result = analyze_source(MULTI, assumptions={"A": True}, telemetry=True)
    assert result.telemetry is not None and result.telemetry.atoms >= 5


def test_atom_free_groups_are_not_components():
    telemetry = analyze_source("#if 1\n#endif\n", telemetry=True).telemetry
    assert telemetry is not None and telemetry.components == () and telemetry.atoms == 0


def test_telemetry_is_deterministic_across_hash_seeds(tmp_path):
    path = tmp_path / "t.c"
    path.write_bytes(MULTI.encode())

    def run(seed):
        return subprocess.run(
            [sys.executable, "-m", "cpre", "--json", "--telemetry", str(path)],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout  # fmt: skip

    assert run("0") == run("1") == run("random")


def test_public_types_are_exported():
    assert cpre.AnalysisTelemetry.__module__ == "cpre.api"
    assert cpre.ComponentTelemetry.__module__ == "cpre.api"


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "t.c"
    path.write_bytes(MULTI.encode())
    return str(path)


def test_cli_json_has_telemetry_only_when_requested(capsys, source_file):
    assert main(["--json", source_file]) == 0
    assert "telemetry" not in json.loads(capsys.readouterr().out)
    assert main(["--json", "--telemetry", source_file]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["telemetry"]) == {"atoms", "components", "limits", "work"}
    assert set(data["telemetry"]["limits"]) == {"max_atoms", "max_bdd_nodes", "max_work"}
    assert all(
        set(c) == {"atoms", "groups", "nodes", "status", "resource", "incomplete_component"}
        for c in data["telemetry"]["components"]
    )
    without = {k: v for k, v in data.items() if k != "telemetry"}
    assert main(["--json", source_file]) == 0
    assert json.loads(capsys.readouterr().out) == without


def test_cli_batch_json_keeps_clean_files_when_telemetry_is_requested(capsys, tmp_path):
    clean = tmp_path / "clean.c"
    clean.write_bytes(b"#if A\n#endif\n")
    other = tmp_path / "other.c"
    other.write_bytes(b"int x;\n")
    assert main(["--json", str(clean), str(other)]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == []
    assert main(["--json", "--telemetry", str(clean), str(other)]) == 0
    files = json.loads(capsys.readouterr().out)["files"]
    assert [f["path"] for f in files] == [str(clean), str(other)]
    assert all("telemetry" in f for f in files)


def test_cli_text_reports_telemetry_on_stderr_and_leaves_stdout_alone(capsys, source_file):
    assert main([source_file]) == 0
    plain = capsys.readouterr()
    assert main(["--telemetry", source_file]) == 0
    traced = capsys.readouterr()
    assert traced.out == plain.out and plain.err == ""
    assert "telemetry: atoms=5 components=3" in traced.err
    assert traced.err.count("component ") == 3


def test_cli_telemetry_conflicts(capsys, source_file):
    for flags in (["--no-conditionals"], ["--sarif"]):
        with pytest.raises(SystemExit) as caught:
            main(["--telemetry", *flags, source_file])
        assert caught.value.code == 2
    capsys.readouterr()
