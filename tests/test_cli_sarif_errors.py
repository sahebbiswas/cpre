import json

from cpre.cli import main


def test_sarif_still_emits_valid_log_when_one_file_has_parse_error(tmp_path, capsys):
    good = tmp_path / "good.c"
    bad = tmp_path / "bad.c"
    good.write_text("#if A && A\n#endif\n", encoding="utf-8")
    bad.write_text("#if B &&\n#endif\n", encoding="utf-8")

    exit_code = main([str(good), str(bad), "--sarif"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert str(bad) in captured.err
    document = json.loads(captured.out)
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert len(run["results"]) == 1
    invocation = run["invocations"][0]
    assert invocation["executionSuccessful"] is False
    notification = invocation["toolExecutionNotifications"][0]
    assert notification["descriptor"]["id"] == "expression_syntax"
    assert notification["locations"][0]["physicalLocation"]["region"] == {
        "startLine": 2,
        "startColumn": 1,
    }
