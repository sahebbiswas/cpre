import json

import cpre

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
        "startLine": 1,
        "startColumn": 7,
    }


def test_sarif_source_read_error_uses_stable_declared_descriptor(tmp_path, capsys):
    missing = tmp_path / "missing.c"

    exit_code = main([str(missing), "--sarif"])

    captured = capsys.readouterr()
    assert exit_code == 2
    document = json.loads(captured.out)
    run = document["runs"][0]
    notification = run["invocations"][0]["toolExecutionNotifications"][0]
    descriptor_id = notification["descriptor"]["id"]
    declared_ids = {
        descriptor["id"] for descriptor in run["tool"]["driver"]["notifications"]
    }

    assert descriptor_id == cpre.ErrorCode.SOURCE_READ_ERROR.value
    assert descriptor_id in declared_ids
