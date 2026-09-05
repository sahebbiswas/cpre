import json

import cpre
import pytest

from cpre.cli import main
from cpre.sarif import sarif_log


def test_sarif_log_has_github_compatible_shape_and_rules():
    analysis = cpre.analyze_source(
        "#if (A && B) || (A && !B)\n#endif\n",
        filename="src/example.c",
    )

    log = sarif_log([analysis], tool_version="0.6.2")

    assert log["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert log["version"] == "2.1.0"
    run = log["runs"][0]
    assert run["tool"]["driver"]["name"] == "cpre"
    assert run["tool"]["driver"]["semanticVersion"] == "0.6.2"
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == [
        "CPRE001",
        "CPRE002",
        "CPRE003",
        "CPRE004",
    ]
    assert run["invocations"] == [{"executionSuccessful": True}]


def test_sarif_exact_simplification_has_precise_location_and_fix():
    analysis = cpre.analyze_source(
        "#if (A && B) || (A && !B)\n#endif\n",
        filename="src/example.c",
    )

    result = sarif_log([analysis], tool_version="0.6.2")["runs"][0]["results"][0]

    assert result["ruleId"] == "CPRE003"
    assert result["level"] == "note"
    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "src/example.c"
    assert physical["region"] == {
        "startLine": 1,
        "startColumn": 5,
        "endLine": 1,
        "endColumn": 26,
    }
    assert result["properties"]["originalCondition"] == "(A && B) || (A && !B)"
    assert result["properties"]["fixConfidence"] == "exact"
    replacement = result["fixes"][0]["artifactChanges"][0]["replacements"][0]
    assert replacement["deletedRegion"] == physical["region"]
    assert replacement["insertedContent"] == {"text": "A"}


def test_sarif_dead_branch_has_warning_without_fix():
    analysis = cpre.analyze_source(
        "#if A || B\n#elif A\n#endif\n",
        filename="src/dead.c",
    )

    result = sarif_log([analysis], tool_version="0.6.2")["runs"][0]["results"][0]

    assert result["ruleId"] == "CPRE001"
    assert result["level"] == "warning"
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 2}
    assert "fixes" not in result


def test_sarif_batch_uses_one_run_with_multiple_artifact_uris():
    left = cpre.analyze_source("#if A && A\n#endif\n", filename="src/left.c")
    right = cpre.analyze_source("#if B && B\n#endif\n", filename="src/right.c")

    run = sarif_log([left, right], tool_version="0.6.2")["runs"][0]

    assert [
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        for result in run["results"]
    ] == ["src/left.c", "src/right.c"]


def test_sarif_incomplete_analysis_is_tool_notification_not_finding():
    analysis = cpre.analyze_source(
        "#if A && B\n#endif\n",
        filename="src/limited.c",
        options=cpre.AnalysisOptions(max_atoms=1),
    )

    run = sarif_log([analysis], tool_version="0.6.2")["runs"][0]

    assert run["results"] == []
    invocation = run["invocations"][0]
    assert invocation["executionSuccessful"] is False
    notification = invocation["toolExecutionNotifications"][0]
    assert notification["descriptor"]["id"] == "analysis_limit_exceeded"
    assert notification["properties"] == {
        "resource": "atoms",
        "limit": 1,
        "observed": 2,
    }


def test_cli_sarif_emits_single_document_for_batch(tmp_path, capsys):
    left = tmp_path / "left.c"
    right = tmp_path / "right.c"
    left.write_text("#if A && A\n#endif\n", encoding="utf-8")
    right.write_text("#if B || B\n#endif\n", encoding="utf-8")

    exit_code = main([str(left), str(right), "--sarif"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    document = json.loads(captured.out)
    assert document["version"] == "2.1.0"
    assert len(document["runs"]) == 1
    assert len(document["runs"][0]["results"]) == 2


def test_cli_json_and_sarif_are_mutually_exclusive(tmp_path, capsys):
    source = tmp_path / "source.c"
    source.write_text("#if A\n#endif\n", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main([str(source), "--json", "--sarif"])

    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
